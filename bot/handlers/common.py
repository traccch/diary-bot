"""/start, /help и настройки: часовой пояс, целевые значения."""

from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from ..db import Database, UserSettings
from ..formatting import esc
from ..keyboards import timezone_choices
from .hub import home_keyboard

#: Пояса, в которых живёт большинство: подсказка вместо ввода строки IANA.
POPULAR_ZONES: tuple[tuple[str, str], ...] = (
    ("Калининград", "Europe/Kaliningrad"),
    ("Москва", "Europe/Moscow"),
    ("Самара", "Europe/Samara"),
    ("Екатеринбург", "Asia/Yekaterinburg"),
    ("Омск", "Asia/Omsk"),
    ("Новосибирск", "Asia/Novosibirsk"),
    ("Красноярск", "Asia/Krasnoyarsk"),
    ("Иркутск", "Asia/Irkutsk"),
    ("Якутск", "Asia/Yakutsk"),
    ("Владивосток", "Asia/Vladivostok"),
)


#: Города по-русски: в именах поясов их нет, а человек пишет именно так.
RU_ALIASES: dict[str, str] = {
    "калининград": "Europe/Kaliningrad",
    "москва": "Europe/Moscow",
    "мск": "Europe/Moscow",
    "питер": "Europe/Moscow",
    "санкт-петербург": "Europe/Moscow",
    "спб": "Europe/Moscow",
    "сочи": "Europe/Moscow",
    "казань": "Europe/Moscow",
    "нижний новгород": "Europe/Moscow",
    "самара": "Europe/Samara",
    "саратов": "Europe/Saratov",
    "волгоград": "Europe/Volgograd",
    "екатеринбург": "Asia/Yekaterinburg",
    "челябинск": "Asia/Yekaterinburg",
    "уфа": "Asia/Yekaterinburg",
    "тюмень": "Asia/Yekaterinburg",
    "пермь": "Asia/Yekaterinburg",
    "омск": "Asia/Omsk",
    "новосибирск": "Asia/Novosibirsk",
    "нск": "Asia/Novosibirsk",
    "барнаул": "Asia/Barnaul",
    "томск": "Asia/Tomsk",
    "кемерово": "Asia/Novokuznetsk",
    "новокузнецк": "Asia/Novokuznetsk",
    "красноярск": "Asia/Krasnoyarsk",
    "абакан": "Asia/Krasnoyarsk",
    "норильск": "Asia/Krasnoyarsk",
    "иркутск": "Asia/Irkutsk",
    "улан-удэ": "Asia/Irkutsk",
    "чита": "Asia/Chita",
    "якутск": "Asia/Yakutsk",
    "владивосток": "Asia/Vladivostok",
    "хабаровск": "Asia/Vladivostok",
    "магадан": "Asia/Magadan",
    "южно-сахалинск": "Asia/Sakhalin",
    "камчатка": "Asia/Kamchatka",
    "петропавловск-камчатский": "Asia/Kamchatka",
    "минск": "Europe/Minsk",
    "киев": "Europe/Kyiv",
    "алматы": "Asia/Almaty",
    "астана": "Asia/Almaty",
    "ташкент": "Asia/Tashkent",
    "бишкек": "Asia/Bishkek",
    "тбилиси": "Asia/Tbilisi",
    "ереван": "Asia/Yerevan",
    "баку": "Asia/Baku",
}


def suggest_zones(query: str, limit: int = 4) -> list[str]:
    """Пояса, похожие на введённое. «Europe/Krasnoyarsk» → «Asia/Krasnoyarsk».

    Материк в имени пояса запомнить невозможно, а город человек знает точно —
    поэтому ищем по городу и предлагаем нажать, а не печатать заново.
    """
    text = query.strip().lower().replace("ё", "е")
    alias = RU_ALIASES.get(text) or RU_ALIASES.get(text.replace("_", " "))
    if alias:
        return [alias]

    text = text.replace(" ", "_")
    city = text.rsplit("/", 1)[-1]
    if not city:
        return []

    zones = available_timezones()
    exact = sorted(zone for zone in zones if zone.lower().rsplit("/", 1)[-1] == city)
    if exact:
        return exact[:limit]
    starts = sorted(
        zone for zone in zones if zone.lower().rsplit("/", 1)[-1].startswith(city)
    )
    return starts[:limit]


def zone_time(zone: str) -> str:
    """Который час в этом поясе — так выбирать вернее, чем по названию."""
    try:
        return dt.datetime.now(ZoneInfo(zone)).strftime("%H:%M")
    except (ZoneInfoNotFoundError, ValueError):
        return ""

router = Router(name="common")

START = """👋 Привет! Я твой личный бот-дневник. Во мне три раздела.

🩺 <b>Давление</b> — измерения, сон, шаги, вес, сводки и PDF для кардиолога.
💰 <b>Деньги</b> — расходы и доходы, категории, лимиты, баланс.
🇬🇧 <b>Английский</b> — карточки и квесты по пять минут в день.

<b>Просто пиши, я разберусь сам:</b>
· <code>120/80 68</code> — давление и пульс
· <code>120/80 68 сон 23:21-7:01</code> — заодно и сон
· <code>кофе 300</code> · <code>такси 450 вчера</code> — траты
· <code>+90000 зарплата</code> — доход, через плюс

Раздел нужен только для команд: /stats и /export покажут то, что открыто.
Цифры я узнаю в любом разделе — давление не попадёт в расходы, и наоборот.

Напоминания я уже включил сам — утром и вечером про давление, вечером про
траты, днём про английский. Не нужно — <code>/remind off</code> в нужном разделе.

Что дальше:
• /menu — выбрать раздел
• /eng — первая сессия английского на три минуты
• <code>/remind 08:00</code> — напоминания (в каждом разделе свои)
• /help — все команды

<i>Я не ставлю диагнозов и не назначаю лечение — я только аккуратно веду записи.</i>"""

HELP = """📔 <b>Личный дневник — как пользоваться</b>

<b>Разделы</b>
/menu — переключить: 🩺 Давление, 💰 Деньги или 🇬🇧 Английский.
Команды ниже с пометкой «в разделе» работают в том, что открыт сейчас.
Свободный текст разбирается независимо от раздела.

<b>🩺 Давление</b>
· <code>120/80</code> · <code>120/80 68</code> · <code>120 80 68</code>
· <code>130/85 вчера</code> · <code>130/85 21:30</code>
· <code>140/90 15.08 09:00 после кофе</code>
/add — пошаговый ввод, /target <code>130/80</code> — целевые значения

<b>Самочувствие</b>
· <code>сон 23:21-7:01</code> · <code>сон 7ч40м</code>
· <code>шаги 8200</code> · <code>пульс покоя 58</code> · <code>вес 78,5</code>
Одно значение на день; в сводке видно связь с давлением.

<b>💰 Деньги</b>
· <code>кофе 300</code> · <code>450 такси вчера</code> · <code>аренда 45к</code>
· <code>+90000 зарплата</code> · <code>+5000 вернули за билет</code> — доходы
/balance — сколько пришло, ушло и осталось за месяц
/cats, /addcat, /delcat, /kw — категории и ключевые слова
/limit <code>30000</code>, /limit <code>Кафе 8000</code>, /limits — лимиты на месяц

<b>🇬🇧 Английский</b>
/eng — сессия карточек: десять слов, три минуты
/quest — сцена из игры или фильма и вопросы по ней
/engstats — прогресс, серия дней, темы
<code>/word loot</code> — перевод и пример; в разделе достаточно прислать слово

<b>Общее (в разделе)</b>
/stats — сводка · /chart — график · /last — последние записи
/undo — удалить последнюю · /del <code>42</code> — по номеру
/export — выгрузка: PDF для врача или CSV для Excel

<b>Напоминания</b>
/remind <code>08:00</code> — в текущем разделе: измерить давление или записать траты
/remind off — выключить в этом разделе · /reminders — список

<b>Голосом</b>
Можно надиктовать: «сто двадцать на восемьдесят, пульс шестьдесят восемь».
Работает, если на компьютере настроен whisper.cpp (см. README).

<b>Настройки и прочее</b>
/tz <code>Europe/Moscow</code> — часовой пояс · /update — обновить бота
/about — о шкале давления и об ограничениях

<b>Как мерить давление, чтобы цифры были честными</b>
Сидя, спина опирается, ноги на полу, 5 минут покоя, манжета на уровне сердца,
не сразу после кофе, курения и нагрузки."""

ABOUT = """ℹ️ <b>О шкале и об ограничениях</b>

Категории — по классификации Европейского общества кардиологов (ESC/ESH):
🟢 оптимальное &lt;120/80 · нормальное 120–129/80–84
🟡 высокое нормальное 130–139/85–89
🟠 АГ 1 степени 140–159/90–99
🔴 АГ 2 степени 160–179/100–109 · АГ 3 степени ≥180/110
🔵 пониженное &lt;90/60

Эти пороги — для измерений на приёме у врача. Дома давление обычно ниже,
поэтому гипертонией считают средние домашние значения от 135/85.
Один высокий замер ничего не доказывает — важна картина за дни и недели,
её и показывает /stats.

Я не врач и не медицинское изделие: я не ставлю диагнозов, не назначаю
и не отменяю лекарства. Если давление ≥180/120, есть боль в груди, одышка,
слабость в руке или ноге, нарушение речи — это скорая (103), а не дневник.

Данные лежат в одном файле SQLite там, где запущен бот, и никуда не уходят."""


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(START, reply_markup=home_keyboard())


@router.message(Command("help", "hub", "do"))
async def cmd_help(message: Message) -> None:
    """Помощь — это не список команд, а кнопки «что сделать»."""
    from .hub import HOME

    await message.answer(HOME, reply_markup=home_keyboard())


@router.message(Command("commands"))
async def cmd_commands(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    await message.answer(ABOUT)


@router.message(Command("target"))
async def cmd_target(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            f"Целевые значения: <b>ниже {user.target_sys}/{user.target_dia}</b>\n"
            "Сменить: <code>/target 130/80</code>\n\n"
            "<i>По умолчанию 135/85 — порог для домашних измерений. "
            "Если врач назвал другую цель, поставь её.</i>"
        )
        return

    match = re.fullmatch(r"(\d{2,3})\s*[/\\ -]\s*(\d{2,3})", raw)
    if not match:
        await message.answer("Нужно два числа: <code>/target 130/80</code>")
        return

    systolic, diastolic = int(match.group(1)), int(match.group(2))
    if not 90 <= systolic <= 200 or not 50 <= diastolic <= 130 or systolic <= diastolic:
        await message.answer("Такая цель выглядит странно. Обычные значения — от 120/70 до 150/90.")
        return

    await db.set_target(user.user_id, systolic, diastolic)
    await message.answer(f"Цель: <b>ниже {systolic}/{diastolic}</b>. Буду считать по ней.")


def tz_text(user: UserSettings) -> str:
    now = zone_time(user.tz)
    return (
        f"🌍 Часовой пояс: <b>{esc(user.tz)}</b>"
        + (f" · сейчас {now}" if now else "")
        + "\n\nВыбери свой город кнопкой — по нему пойдут напоминания.\n"
        "<i>Города нет в списке? Пришли <code>/tz Иркутск</code> — найду.</i>"
    )


@router.message(Command("tz"))
async def cmd_tz(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    value = (command.args or "").strip()
    if not value:
        await message.answer(
            tz_text(user), reply_markup=timezone_choices(POPULAR_ZONES, zone_time)
        )
        return

    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        found = suggest_zones(value)
        if found:
            await message.answer(
                f"Пояса <code>{esc(value)}</code> нет, но, кажется, ты про это:",
                reply_markup=timezone_choices(
                    [(zone.rsplit("/", 1)[-1].replace("_", " "), zone) for zone in found],
                    zone_time,
                ),
            )
            return
        await message.answer(
            "Не знаю такого часового пояса. Попробуй просто город — "
            "<code>/tz Красноярск</code> — или выбери кнопкой в /tz."
        )
        return

    await db.set_tz(user.user_id, value)
    await message.answer(
        f"✅ Часовой пояс: <b>{esc(value)}</b> · сейчас {zone_time(value)}.\n"
        "По нему теперь и напоминания."
    )


@router.callback_query(F.data.startswith("tz:"))
async def cb_tz(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    zone = (callback.data or "").split(":", 1)[1]
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        await callback.answer("Не знаю такого пояса", show_alert=True)
        return

    await db.set_tz(user.user_id, zone)
    await callback.answer(f"Часовой пояс: {zone}")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"✅ Часовой пояс: <b>{esc(zone)}</b> · сейчас {zone_time(zone)}.\n"
            "По нему теперь и напоминания.",
            reply_markup=None,
        )
