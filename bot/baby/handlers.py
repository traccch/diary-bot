"""Раздел «Малыш»: экраны, кнопки, разбор текста и приглашение для семьи.

Приглашённый (роль «семья») видит только этот раздел. Всё, что он пишет
текстом, разбирается здесь: «уснул», «проснулся 7:05», «сон 13:20-14:45»,
«рост 76 вес 9,4 голова 45», «город Казань». У хозяина бота текст «сон …»
и «вес …» занят его собственным дневником, поэтому сон задним числом и
замеры он вносит кнопками — а «уснул» и «проснулся» понимаются и так.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import re
from typing import Any, Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from .. import sections
from ..db import Database, UserSettings
from . import charts, growth, keyboards, norms, sleep, weather
from .db import FAMILY, OWNER, Child

logger = logging.getLogger(__name__)
router = Router(name="baby")

INVITE_PREFIX = "fam_"
#: Во сколько по умолчанию приходит утренний прогноз.
WEATHER_AT = dt.time(8, 30)

TEXT_HINT = (
    "Можно писать текстом:\n"
    "· <code>уснул</code> · <code>проснулся</code> — прямо сейчас\n"
    "· <code>уснул 13:20</code> · <code>проснулся в 7:05</code>\n"
    "· <code>сон 13:20-14:45</code> — если забыли отметить\n"
    "· <code>рост 76 вес 9,4 голова 45</code> — замеры у педиатра\n"
    "· <code>город Казань</code> — для погоды"
)


class BabySetup(StatesGroup):
    name = State()
    birth = State()


class BabyInput(StatesGroup):
    past = State()  # ждём «13:20-14:45»
    measure = State()  # ждём замеры
    city = State()  # ждём город


async def family_only(event: Any, db: Database) -> bool:
    """Пишет приглашённый? Фильтры срабатывают раньше мидлварей — спрашиваем базу сами."""
    sender = getattr(event, "from_user", None)
    return sender is not None and await db.baby_role(sender.id) == FAMILY


# ------------------------------------------------------------- состояние


async def _child_for(db: Database, user: UserSettings) -> tuple[Optional[Child], Optional[str]]:
    return await db.baby_of(user.user_id)


def _he(child: Child, male: str, female: str) -> str:
    return female if child.sex == "f" else male


async def status_lines(db: Database, child: Child, now: dt.datetime) -> list[str]:
    """Спит или нет, сколько, когда укладывать, итог дня."""
    norm = norms.norm_for(norms.age_months(child.birth, now.date()))
    lines: list[str] = []
    open_sleep = await db.baby_open_sleep(child.id)
    if open_sleep is not None:
        lines.append(
            f"😴 <b>Спит {norms.duration_text(now - open_sleep.start)}</b>"
            f" — с {sleep.clock(open_sleep.start)}"
        )
    else:
        woke = await db.baby_last_wake(child.id)
        if woke is not None and now - woke < dt.timedelta(hours=14):
            lines.append(
                f"🌞 <b>Не спит {norms.duration_text(now - woke)}</b> — с {sleep.clock(woke)}"
            )
            early, late = norms.bedtime_window(woke, norm)
            if now < early:
                lines.append(
                    f"Укладывать примерно в <b>{sleep.clock(early)}–{sleep.clock(late)}</b>"
                    f" <i>(окно бодрствования в этом возрасте — {norms.hours_text(*norm.wake_hours)})</i>"
                )
            elif now <= late:
                lines.append(
                    f"⏰ <b>Пора укладывать</b> — до {sleep.clock(late)}"
                    f" <i>(окно {norms.hours_text(*norm.wake_hours)})</i>"
                )
            else:
                lines.append(
                    f"⏰ Окно бодрствования ({norms.hours_text(*norm.wake_hours)}) уже прошло — "
                    "возможно, устал"
                )
        else:
            lines.append("Сон ещё не отмечали — нажми «😴 Уснул», когда уснёт.")

    sleeps = await db.baby_sleeps(child.id, now - dt.timedelta(hours=36))
    summary = sleep.summarize(sleeps, now)
    pieces = []
    if summary.night is not None:
        pieces.append(f"ночь {norms.duration_text(summary.night.length(now))}")
    if summary.naps:
        pieces.append(f"днём {len(summary.naps)} × {norms.duration_text(summary.nap_time)}")
    if pieces:
        lines.append("Сегодня: " + ", ".join(pieces))
    return lines


async def show_menu(message: Message, db: Database, user: UserSettings, now: dt.datetime) -> None:
    child, role = await _child_for(db, user)
    if child is None:
        await message.answer(
            "👶 <b>Малыш</b>\n\n"
            "Здесь можно вести сон и бодрствование малыша, замеры у педиатра "
            "с нормами ВОЗ и смотреть, как одеть его на прогулку по погоде.\n\n"
            "Сначала заведём малыша: имя, дата рождения, мальчик или девочка.",
            reply_markup=keyboards.setup_start(),
        )
        return
    open_sleep = await db.baby_open_sleep(child.id)
    lines = [
        f"👶 <b>{html.escape(child.name)}</b> · {norms.age_text(child.birth, now.date())}",
        "",
        *await status_lines(db, child, now),
    ]
    await message.answer(
        "\n".join(lines), reply_markup=keyboards.menu(open_sleep is not None, role == OWNER)
    )


# ------------------------------------------------------------- сон


async def fell_asleep(
    message: Message, db: Database, child: Child, at: dt.datetime, by: int, now: dt.datetime
) -> None:
    open_sleep = await db.baby_open_sleep(child.id)
    if open_sleep is not None:
        await message.answer(
            f"😴 Уже спит — с {sleep.clock(open_sleep.start)}. Проснётся — нажми «🌞 Проснулся».",
            reply_markup=keyboards.after_action(True),
        )
        return
    woke = await db.baby_last_wake(child.id)
    if woke is not None and at < woke:
        await message.answer(
            f"Не сходится: последнее пробуждение записано в {sleep.clock(woke)}. "
            "Проверь время или отмени последнюю запись.",
            reply_markup=keyboards.back(),
        )
        return
    await db.baby_start_sleep(child.id, at, by)
    text = f"😴 {_he(child, 'Уснул', 'Уснула')} в <b>{sleep.clock(at)}</b>"
    if woke is not None and at - woke < dt.timedelta(hours=14):
        text += f"\nНе {_he(child, 'спал', 'спала')} до этого {norms.duration_text(at - woke)}"
    await message.answer(text, reply_markup=keyboards.after_action(True))


async def woke_up(
    message: Message, db: Database, child: Child, at: dt.datetime, now: dt.datetime
) -> None:
    open_sleep = await db.baby_open_sleep(child.id)
    if open_sleep is None:
        await message.answer(
            "Не вижу, когда уснул. Запиши сон целиком, например "
            "<code>сон 13:20-14:45</code>, или кнопкой «✍️ Сон задним числом».",
            reply_markup=keyboards.back(),
        )
        return
    if at < open_sleep.start:
        await message.answer(
            f"Не сходится: уснул в {sleep.clock(open_sleep.start)}, а проснулся раньше. "
            "Проверь время.",
            reply_markup=keyboards.back(),
        )
        return
    await db.baby_end_sleep(open_sleep.id, at)
    finished = sleep.Sleep(open_sleep.id, open_sleep.start, at)
    norm = norms.norm_for(norms.age_months(child.birth, now.date()))
    early, late = norms.bedtime_window(at, norm)
    lines = [
        f"🌞 {_he(child, 'Проснулся', 'Проснулась')} в <b>{sleep.clock(at)}</b>",
        f"{_he(child, 'Спал', 'Спала')} {norms.duration_text(finished.length(now))}"
        f" ({'ночь' if finished.is_night(now) else 'дневной сон'})",
    ]
    if at.hour < 19:
        lines.append(f"Следующий сон — примерно {sleep.clock(early)}–{sleep.clock(late)}")
    await message.answer("\n".join(lines), reply_markup=keyboards.after_action(False))


async def add_range(
    message: Message, db: Database, child: Child, start: dt.datetime, end: dt.datetime, by: int, now: dt.datetime
) -> None:
    if end - start > dt.timedelta(hours=16):
        await message.answer("Сон дольше 16 часов — похоже на опечатку. Проверь время.")
        return
    added = await db.baby_add_sleep(child.id, start, end, by)
    await message.answer(
        f"✍️ Записал сон {sleep.clock(start)}–{sleep.clock(end)}:"
        f" {norms.duration_text(added.length(now))}",
        reply_markup=keyboards.back(),
    )


async def day_report(message: Message, db: Database, child: Child, now: dt.datetime) -> None:
    norm = norms.norm_for(norms.age_months(child.birth, now.date()))
    sleeps = await db.baby_sleeps(child.id, now - dt.timedelta(hours=36))
    summary = sleep.summarize(sleeps, now)
    lines = [f"📊 <b>Сон сегодня</b> · {html.escape(child.name)}, {norms.age_text(child.birth, now.date())}", ""]

    if summary.night is not None:
        night_hours = norms.as_hours(summary.night.length(now))
        ongoing = " (ещё спит)" if summary.night.end is None else ""
        lines.append(
            f"🌙 Ночь: <b>{norms.duration_text(summary.night.length(now))}</b>{ongoing}"
            f" · {sleep.clock(summary.night.start)}–"
            f"{sleep.clock(summary.night.end) if summary.night.end else '…'}"
            f" — {norms.verdict(night_hours, norm.night_hours)}"
            f" <i>(норма {norms.hours_text(*norm.night_hours)})</i>"
        )
    else:
        lines.append("🌙 Ночь не записана")

    nap_hours = norms.as_hours(summary.nap_time)
    lines.append(
        f"☀️ Днём: <b>{len(summary.naps)}</b> × {norms.duration_text(summary.nap_time)}"
        f" <i>(обычно {norm.naps_text} сна, {norms.hours_text(*norm.day_hours)})</i>"
    )
    for nap in summary.naps:
        end = sleep.clock(nap.end) if nap.end else "…"
        lines.append(f"   · {sleep.clock(nap.start)}–{end}  {norms.duration_text(nap.length(now))}")

    total_hours = norms.as_hours(summary.last_24h)
    lines.append(
        f"⏱ За последние 24 часа: <b>{norms.duration_text(summary.last_24h)}</b>"
        f" — {norms.verdict(total_hours, norm.total_hours)}"
        f" <i>(норма {norms.hours_text(*norm.total_hours)})</i>"
    )
    lines.append("")
    lines.append(
        "<i>Нормы — ориентиры AASM и педиатров; у каждого ребёнка свой ритм. "
        "Если малыш бодрый и хорошо ест, небольшие отклонения не страшны.</i>"
    )
    await message.answer("\n".join(lines), reply_markup=keyboards.back())


# ---------------------------------------------------------- замеры


async def save_growth(
    message: Message, db: Database, child: Child, entry: growth.Entry, by: int
) -> None:
    await db.baby_add_growth(child.id, entry.on, entry.values, by)
    lines = [f"📏 <b>Записал замеры</b> на {entry.on:%d.%m.%Y}", ""]
    for kind in (growth.WEIGHT, growth.LENGTH, growth.HEAD):
        if kind in entry.values:
            lines.append(growth.describe(kind, child.sex, child.birth, entry.on, entry.values[kind]))
    lines.append("")
    lines.append("<i>Нормы — стандарты роста ВОЗ. Это ориентир, а не диагноз: выводы — у педиатра.</i>")
    await message.answer("\n".join(lines), reply_markup=keyboards.growth_actions())


async def growth_report(message: Message, db: Database, child: Child, today: dt.date) -> None:
    points = await db.baby_growth(child.id)
    if not points:
        await message.answer(
            "📏 <b>Рост и вес</b>\n\nЗамеров пока нет. После педиатра пришли одной строкой:\n"
            "<code>рост 76 вес 9,4 голова 45</code>\n"
            "Можно с датой: <code>рост 76 вес 9,4 15.11</code>. Бот сравнит с нормами ВОЗ.",
            reply_markup=keyboards.growth_actions(),
        )
        return
    latest: dict[str, tuple[dt.date, float]] = {}
    for on, kind, value in points:
        latest[kind] = (on, value)
    lines = [f"📏 <b>Рост и вес</b> · {html.escape(child.name)}", ""]
    for kind in (growth.WEIGHT, growth.LENGTH, growth.HEAD):
        if kind in latest:
            on, value = latest[kind]
            lines.append(growth.describe(kind, child.sex, child.birth, on, value) + f" · {on:%d.%m}")
    measured_days = sorted({on for on, _, _ in points})
    lines.append("")
    lines.append(f"Всего замеров: {len(measured_days)}, последний — {measured_days[-1]:%d.%m.%Y}")
    await message.answer("\n".join(lines), reply_markup=keyboards.growth_actions())


# ------------------------------------------------------------- погода


async def weather_text(child: Child, now: dt.datetime, fetch=None) -> str:
    if child.lat is None or child.lon is None:
        return (
            "🌤 Город ещё не указан. Пришли <code>город Казань</code> "
            "или отправь геопозицию (скрепка → Геопозиция)."
        )
    try:
        if fetch is None:
            found = await weather.forecast(child.lat, child.lon)
        else:
            found = await weather.forecast(child.lat, child.lon, fetch=fetch)
    except weather.WeatherError as exc:
        return f"🌤 Не получилось узнать погоду: {exc}."
    return weather.render(child.city or "Сейчас", found, now, _name_gen(child))


def _name_gen(child: Child) -> str:
    """Имя в родительном падеже — грубо, для подписи «что надеть для Миши»."""
    name = child.name.strip()
    if not name or not re.fullmatch(r"[А-Яа-яЁё]+", name):
        return ""
    if name[-1] in "аяАЯ":
        stem = name[:-1]
        return stem + ("и" if stem[-1:] in "гкхжчшщ" or name[-1] == "я" else "ы")
    if name[-1] in "йь":
        return name[:-1] + "я"
    return name + "а"


async def set_city(message: Message, db: Database, child: Child, name: str, user_id: int) -> None:
    try:
        place = await weather.find_place(name)
    except weather.WeatherError as exc:
        await message.answer(f"🏙 {str(exc).capitalize()}. Попробуй иначе или пришли геопозицию.")
        return
    await db.baby_update(child.id, city=place.name, lat=place.lat, lon=place.lon)
    await _ensure_weather_reminders(db, child.id, user_id)
    await message.answer(
        f"🏙 Город: <b>{html.escape(place.name)}</b>. "
        f"Прогноз и совет по одежде будут приходить каждое утро в {WEATHER_AT:%H:%M} "
        "(выключить — в ⚙️ Настройки).",
        reply_markup=keyboards.back(),
    )


async def _ensure_weather_reminders(db: Database, child_id: int, setter: int) -> None:
    """Утренний прогноз — тому, кто указал город, и всем приглашённым."""
    for user_id, role in await db.baby_members(child_id):
        if user_id != setter and role != FAMILY:
            continue
        if not await db.list_reminders(user_id, sections.BABY):
            await db.add_reminder(user_id, WEATHER_AT, sections.BABY)


# --------------------------------------------------------- разбор текста


async def try_text(
    message: Message,
    text: str,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    full: bool,
) -> bool:
    """Запись про малыша из текста. `full` — понимать всё (у приглашённого),
    иначе только «уснул/проснулся» (у хозяина «сон» и «вес» — его собственные)."""
    child, _ = await _child_for(db, user)
    if child is None:
        return False
    command = sleep.parse(text, now)
    if command is not None and (full or command.kind != sleep.RANGE):
        if command.kind == sleep.START:
            await fell_asleep(message, db, child, command.start, user.user_id, now)
        elif command.kind == sleep.END:
            await woke_up(message, db, child, command.end, now)
        else:
            await add_range(message, db, child, command.start, command.end, user.user_id, now)
        return True
    if not full:
        return False

    entry = growth.parse(text, now.date())
    if entry is not None:
        await save_growth(message, db, child, entry, user.user_id)
        return True

    city = re.match(r"^\s*город\s+(.{2,60})$", text, re.IGNORECASE)
    if city:
        await set_city(message, db, child, city.group(1), user.user_id)
        return True
    return False


# ---------------------------------------------------------- приглашение


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(INVITE_PREFIX)))
async def on_invite(
    message: Message,
    command: CommandObject,
    db: Database,
    user: UserSettings,
    bot: Bot,
    now: dt.datetime,
) -> None:
    code = (command.args or "")[len(INVITE_PREFIX):]
    child = await db.baby_join(user.user_id, code)
    if child is None:
        role = await db.baby_role(user.user_id)
        if role is not None:
            await show_menu(message, db, user, now)
            return
        await message.answer("Ссылка уже использована или устарела. Попроси новую.")
        return
    if child.lat is not None and not await db.list_reminders(user.user_id, sections.BABY):
        await db.add_reminder(user.user_id, WEATHER_AT, sections.BABY)  # город уже указан
    await message.answer(
        f"👋 Привет! Это дневник малыша — <b>{html.escape(child.name)}</b>.\n\n"
        "Отмечай, когда уснул и проснулся, — я посчитаю сон и бодрствование, "
        "подскажу, когда укладывать, сравню замеры с нормами ВОЗ и посоветую, "
        "как одеть на прогулку по погоде.\n\n" + TEXT_HINT
    )
    await show_menu(message, db, user, now)
    for member_id, role in await db.baby_members(child.id):
        if role == OWNER and member_id != user.user_id:
            name = html.escape(message.from_user.full_name if message.from_user else "кто-то")
            try:
                await bot.send_message(member_id, f"👶 К дневнику малыша присоединилась: {name}")
            except Exception:  # noqa: BLE001 - уведомление не главное
                logger.debug("Не уведомил хозяина о новом участнике", exc_info=True)


# --------------------------------------------------------------- кнопки


@router.message(Command("baby", "kid", "malysh"))
async def cmd_baby(message: Message, db: Database, user: UserSettings, now: dt.datetime) -> None:
    await show_menu(message, db, user, now)


@router.callback_query(F.data == "baby:menu")
async def cb_menu(callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    if isinstance(callback.message, Message):
        await show_menu(callback.message, db, user, now)


@router.callback_query(F.data == "baby:setup")
async def cb_setup(callback: CallbackQuery, db: Database, user: UserSettings, state: FSMContext) -> None:
    await callback.answer()
    if await db.baby_role(user.user_id) is not None or not isinstance(callback.message, Message):
        return
    await state.set_state(BabySetup.name)
    await callback.message.answer("Как зовут малыша?")


@router.message(BabySetup.name, F.text, ~F.text.startswith("/"))
async def setup_name(message: Message, state: FSMContext) -> None:
    name = " ".join((message.text or "").split())[:40]
    if not name:
        await message.answer("Напиши имя одним словом.")
        return
    await state.update_data(name=name)
    await state.set_state(BabySetup.birth)
    await message.answer("Дата рождения? Например, <code>22.12.2025</code>")


@router.message(BabySetup.birth, F.text, ~F.text.startswith("/"))
async def setup_birth(message: Message, state: FSMContext, now: dt.datetime) -> None:
    match = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s*$", message.text or "")
    birth = None
    if match:
        year = int(match.group(3))
        year += 2000 if year < 100 else 0
        try:
            birth = dt.date(year, int(match.group(2)), int(match.group(1)))
        except ValueError:
            birth = None
    if birth is None or birth > now.date() or (now.date() - birth).days > 365 * 6:
        await message.answer("Не понял дату. Пример: <code>22.12.2025</code>")
        return
    await state.update_data(birth=birth.isoformat())
    await message.answer("Мальчик или девочка?", reply_markup=keyboards.sex_choice())


@router.callback_query(F.data.startswith("baby:sex:"))
async def setup_sex(
    callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime, state: FSMContext
) -> None:
    await callback.answer()
    data = await state.get_data()
    if not data.get("name") or not data.get("birth") or not isinstance(callback.message, Message):
        return
    if await db.baby_role(user.user_id) is None:
        await db.baby_create(
            user.user_id, data["name"], callback.data.rsplit(":", 1)[1],
            dt.date.fromisoformat(data["birth"]),
        )
    await state.clear()
    await callback.message.answer(
        "👶 Готово! Теперь отмечай сон кнопками или текстом. "
        "Пригласить жену — ⚙️ Настройки → «👩 Пригласить жену».\n\n"
        "Чтобы приходил прогноз и совет по одежде, пришли город: <code>город Казань</code> "
        "(кнопка «🏙 Город для погоды» в настройках)."
    )
    await show_menu(callback.message, db, user, now)


async def _need_child(callback: CallbackQuery, db: Database, user: UserSettings) -> Optional[Child]:
    child, _ = await _child_for(db, user)
    if child is None:
        await callback.answer("Сначала заведи малыша", show_alert=True)
        return None
    await callback.answer()
    if not isinstance(callback.message, Message):
        return None
    return child


@router.callback_query(F.data == "baby:sleep")
async def cb_sleep(callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime) -> None:
    child = await _need_child(callback, db, user)
    if child is not None:
        await fell_asleep(callback.message, db, child, now, user.user_id, now)


@router.callback_query(F.data == "baby:wake")
async def cb_wake(callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime) -> None:
    child = await _need_child(callback, db, user)
    if child is not None:
        await woke_up(callback.message, db, child, now, now)


@router.callback_query(F.data == "baby:day")
async def cb_day(callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime) -> None:
    child = await _need_child(callback, db, user)
    if child is not None:
        await day_report(callback.message, db, child, now)


@router.callback_query(F.data == "baby:week")
async def cb_week(callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime) -> None:
    child = await _need_child(callback, db, user)
    if child is None:
        return
    sleeps = await db.baby_sleeps(child.id, now - dt.timedelta(days=8))
    if not sleeps:
        await callback.message.answer("За неделю сон не записан — рисовать нечего.", reply_markup=keyboards.back())
        return
    if not charts.available():
        await day_report(callback.message, db, child, now)
        return
    image = charts.week_png(sleeps, now, child.name)
    await callback.message.answer_photo(
        BufferedInputFile(image, filename="sleep.png"),
        caption="📅 Сон за неделю: тёмное — ночь, светлое — дневные сны",
        reply_markup=keyboards.back(),
    )


@router.callback_query(F.data == "baby:weather")
async def cb_weather(callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime) -> None:
    child = await _need_child(callback, db, user)
    if child is not None:
        await callback.message.answer(await weather_text(child, now), reply_markup=keyboards.back())


@router.callback_query(F.data == "baby:growth")
async def cb_growth(callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime) -> None:
    child = await _need_child(callback, db, user)
    if child is not None:
        await growth_report(callback.message, db, child, now.date())


@router.callback_query(F.data == "baby:growthchart")
async def cb_growth_chart(callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime) -> None:
    child = await _need_child(callback, db, user)
    if child is None:
        return
    points = await db.baby_growth(child.id)
    if not points or not charts.available():
        await growth_report(callback.message, db, child, now.date())
        return
    image = charts.growth_png(points, child.birth, child.sex, child.name, now.date())
    await callback.message.answer_photo(
        BufferedInputFile(image, filename="growth.png"),
        caption="📈 Замеры на фоне норм ВОЗ",
        reply_markup=keyboards.back(),
    )


@router.callback_query(F.data.in_({"baby:past", "baby:measure", "baby:city"}))
async def cb_ask(callback: CallbackQuery, db: Database, user: UserSettings, state: FSMContext) -> None:
    child = await _need_child(callback, db, user)
    if child is None:
        return
    kind = callback.data.split(":")[1]
    if kind == "past":
        await state.set_state(BabyInput.past)
        text = "Во сколько уснул и проснулся? Например: <code>13:20-14:45</code>"
    elif kind == "measure":
        await state.set_state(BabyInput.measure)
        text = "Пришли замеры: <code>рост 76 вес 9,4 голова 45</code> (можно не все, можно с датой 15.11)"
    else:
        await state.set_state(BabyInput.city)
        text = "Какой город? Например: <code>Казань</code>. Или отправь геопозицию."
    await callback.message.answer(text, reply_markup=keyboards.back())


@router.message(BabyInput.past, F.text, ~F.text.startswith("/"))
async def input_past(message: Message, db: Database, user: UserSettings, now: dt.datetime, state: FSMContext) -> None:
    child, _ = await _child_for(db, user)
    command = sleep.parse(f"сон {message.text}", now)
    if child is None or command is None:
        await message.answer("Не понял. Пример: <code>13:20-14:45</code>")
        return
    await state.clear()
    await add_range(message, db, child, command.start, command.end, user.user_id, now)


@router.message(BabyInput.measure, F.text, ~F.text.startswith("/"))
async def input_measure(message: Message, db: Database, user: UserSettings, now: dt.datetime, state: FSMContext) -> None:
    child, _ = await _child_for(db, user)
    text = message.text or ""
    entry = growth.parse(text, now.date())
    if entry is None:
        # «76 9,4 45» — без слов, в привычном у педиатра порядке
        numbers = re.findall(r"\d+(?:[.,]\d+)?", text)
        if len(numbers) >= 2:
            names = ["рост", "вес", "голова"]
            entry = growth.parse(" ".join(f"{n} {v}" for n, v in zip(names, numbers)), now.date())
    if child is None or entry is None:
        await message.answer("Не понял. Пример: <code>рост 76 вес 9,4 голова 45</code>")
        return
    await state.clear()
    await save_growth(message, db, child, entry, user.user_id)


@router.message(BabyInput.city, F.text, ~F.text.startswith("/"))
async def input_city(message: Message, db: Database, user: UserSettings, state: FSMContext) -> None:
    child, _ = await _child_for(db, user)
    await state.clear()
    if child is not None:
        await set_city(message, db, child, message.text or "", user.user_id)


@router.message(F.location)
async def on_location(message: Message, db: Database, user: UserSettings, state: FSMContext) -> None:
    child, _ = await _child_for(db, user)
    if child is None or message.location is None:
        return
    await state.clear()
    await db.baby_update(
        child.id, city="По геопозиции", lat=message.location.latitude, lon=message.location.longitude
    )
    await _ensure_weather_reminders(db, child.id, user.user_id)
    await message.answer(
        f"📍 Запомнил место. Прогноз и совет по одежде — каждое утро в {WEATHER_AT:%H:%M}.",
        reply_markup=keyboards.back(),
    )


@router.callback_query(F.data == "baby:undo")
async def cb_undo(callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime) -> None:
    child = await _need_child(callback, db, user)
    if child is None:
        return
    last = await db.baby_last_sleep(child.id)
    if last is None:
        await callback.message.answer("Отменять нечего.", reply_markup=keyboards.back())
        return
    await db.baby_delete_sleep(child.id, last.id)
    end = sleep.clock(last.end) if last.end else "…"
    await callback.message.answer(
        f"↩️ Удалил запись сна {sleep.clock(last.start)}–{end}.", reply_markup=keyboards.back()
    )


@router.callback_query(F.data == "baby:settings")
async def cb_settings(callback: CallbackQuery, db: Database, user: UserSettings, bot: Bot) -> None:
    child = await _need_child(callback, db, user)
    if child is None:
        return
    await _settings_screen(callback.message, db, user, child)


async def _settings_screen(message: Message, db: Database, user: UserSettings, child: Child) -> None:
    role = await db.baby_role(user.user_id)
    weather_on = any(r.enabled for r in await db.list_reminders(user.user_id, sections.BABY))
    members = [
        (member_id, member_role, f"id {member_id}")
        for member_id, member_role in await db.baby_members(child.id)
        if member_role == FAMILY
    ]
    lines = [
        "⚙️ <b>Настройки малыша</b>",
        "",
        f"Город: {html.escape(child.city) if child.city else 'не указан'}",
        f"Погода утром: {'приходит в ' + WEATHER_AT.strftime('%H:%M') if weather_on else 'выключена'}",
    ]
    if role == OWNER:
        lines.append(f"Приглашённых: {len(members)}")
    lines.append("")
    lines.append(TEXT_HINT)
    await message.answer(
        "\n".join(lines), reply_markup=keyboards.settings(role == OWNER, weather_on, members)
    )


@router.callback_query(F.data == "baby:wtoggle")
async def cb_weather_toggle(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    child = await _need_child(callback, db, user)
    if child is None:
        return
    reminders = await db.list_reminders(user.user_id, sections.BABY)
    if not reminders:
        await db.add_reminder(user.user_id, WEATHER_AT, sections.BABY)
    else:
        await db.set_topic_reminders(user.user_id, sections.BABY, not any(r.enabled for r in reminders))
    await _settings_screen(callback.message, db, user, child)


@router.callback_query(F.data == "baby:invite")
async def cb_invite(callback: CallbackQuery, db: Database, user: UserSettings, bot: Bot) -> None:
    child = await _need_child(callback, db, user)
    if child is None or await db.baby_role(user.user_id) != OWNER:
        return
    code = await db.baby_invite(child.id)
    payload = f"{INVITE_PREFIX}{code}"
    try:
        me = await bot.me()
        username = getattr(me, "username", None)
    except Exception:  # noqa: BLE001 - без имени бота дадим команду
        username = None
    if username:
        link = f"https://t.me/{username}?start={payload}"
        how = f"Перешли жене эту ссылку — она откроет бота и сразу попадёт к малышу:\n\n{link}"
    else:
        how = f"Пусть жена найдёт бота в Telegram и отправит ему:\n\n<code>/start {payload}</code>"
    await callback.message.answer(
        f"👩 <b>Приглашение</b>\n\n{how}\n\n"
        "Ссылка одноразовая. Жена увидит только раздел малыша — ни английского, "
        "ни давления, ни денег. Убрать доступ можно здесь же, в настройках.",
        reply_markup=keyboards.back(),
    )


@router.callback_query(F.data.startswith("baby:kick:"))
async def cb_kick(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    child = await _need_child(callback, db, user)
    if child is None or await db.baby_role(user.user_id) != OWNER:
        return
    target = int(callback.data.rsplit(":", 1)[1])
    removed = await db.baby_remove_member(child.id, target)
    await callback.message.answer(
        "🚫 Доступ убран." if removed else "Такого участника уже нет.", reply_markup=keyboards.back()
    )


# ------------------------------------------------- приглашённый: всё остальное


@router.message(family_only, F.text, ~F.text.startswith("/"))
async def family_text(message: Message, db: Database, user: UserSettings, now: dt.datetime) -> None:
    if await try_text(message, message.text or "", db, user, now, full=True):
        return
    await message.answer("Не понял 🙂\n\n" + TEXT_HINT, reply_markup=keyboards.back())


@router.message(family_only, F.voice)
async def family_voice(
    message: Message, bot: Bot, db: Database, user: UserSettings, now: dt.datetime, transcriber
) -> None:
    from ..handlers.router import hear

    if not transcriber.ready:
        await message.answer("🎤 Голосовые пока не разбираю — напиши текстом.\n\n" + TEXT_HINT)
        return
    heard = await hear(message, bot, transcriber)
    if heard is None:
        return
    text = heard.strip().strip(".!")
    await message.answer(f"🎤 <i>Расслышал:</i> {html.escape(text)}")
    if not await try_text(message, text, db, user, now, full=True):
        await message.answer("Не понял 🙂\n\n" + TEXT_HINT, reply_markup=keyboards.back())


async def family_fallback(event, data: dict) -> None:
    """Приглашённый нажал или набрал то, что ему не положено, — показываем малыша."""
    message = event if isinstance(event, Message) else getattr(event, "message", None)
    if isinstance(event, CallbackQuery):
        try:
            await event.answer()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(message, Message):
        await show_menu(message, data["db"], data["user"], data["now"])


def family_may(event) -> bool:
    """Что приглашённому можно: кнопки малыша, текст, голос, геопозиция, /start."""
    if isinstance(event, CallbackQuery):
        return (event.data or "").startswith("baby:")
    if isinstance(event, Message):
        text = event.text or ""
        if text.startswith("/"):
            return text.split()[0].split("@")[0] in {"/start", "/baby"} and " " in text
        return True
    return False
