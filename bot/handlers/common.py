"""/start, /help и настройки: часовой пояс, целевые значения."""

from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from ..db import Database, UserSettings
from ..formatting import esc
from .hub import home_keyboard

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


@router.message(Command("tz"))
async def cmd_tz(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    value = (command.args or "").strip()
    if not value:
        await message.answer(
            f"Часовой пояс: <b>{esc(user.tz)}</b>\nСменить: <code>/tz Europe/Berlin</code>"
        )
        return
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        await message.answer(
            "Не знаю такой часовой пояс. Нужен формат IANA, например "
            "<code>Europe/Moscow</code> или <code>Asia/Almaty</code>."
        )
        return
    await db.set_tz(user.user_id, value)
    await message.answer(
        f"Часовой пояс: <b>{esc(value)}</b>. По нему же теперь и напоминания."
    )
