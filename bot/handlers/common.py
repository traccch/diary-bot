"""/start, /help и настройки: часовой пояс, целевые значения."""

from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from ..db import Database, UserSettings
from ..formatting import esc

router = Router(name="common")

START = """👋 Привет! Я твой дневник давления.

<b>Записать измерение</b> — просто пришли цифры:
<code>120/80</code> · <code>120/80 68</code> · <code>135/85 72 после прогулки</code>

Я разберу давление, пульс, время и комментарий, покажу категорию по шкале
кардиологов и сложу всё в дневник. Потом — сводка, графики и выгрузка для врача.

<b>Заодно можно вести самочувствие</b> — в той же строке или отдельно:
<code>120/80 68 сон 23:21-7:01</code> · <code>шаги 8200 пульс покоя 58</code> · <code>вес 78,5</code>
Тогда в сводке видно, как давление зависит от сна и подвижности.

Что дальше:
• <code>/remind 08:00</code> — чтобы я напоминал измерять
• /stats — сводка, /chart — графики
• /export — PDF для кардиолога
• /help — все команды

<i>Я не ставлю диагнозов и не назначаю лечение — я только аккуратно веду записи.</i>"""

HELP = """🩺 <b>Дневник давления — как пользоваться</b>

<b>Записать измерение</b>
Пришли сообщение, порядок слов любой:
· <code>120/80</code> — только давление
· <code>120/80 68</code> — с пульсом
· <code>120 80 68</code> — можно через пробелы
· <code>120/80 п68</code> — пульс явно
· <code>130/85 вчера</code> — вчерашний замер
· <code>130/85 21:30</code> — своё время
· <code>140/90 15.08 09:00 после кофе</code> — дата, время и комментарий
Всё, что не похоже на цифры и дату, попадает в комментарий.

/add — пошаговый ввод, если не хочется вспоминать формат.

<b>Самочувствие рядом с давлением</b>
Можно в одной строке с измерением, можно отдельным сообщением:
· <code>сон 23:21-7:01</code> — утром, время по браслету
· <code>сон 7ч40м</code> — если помнишь только длительность
· <code>шаги 8200</code> — вечером
· <code>пульс покоя 58</code> — он же «пп 58», не путается с пульсом на замере
· <code>вес 78,5</code> — когда встал на весы
Всё это — одно значение на день, повторная запись заменяет прежнюю.
В /stats появится блок «Здоровье» и сравнение: как давление отличается
в дни с коротким сном и в малоподвижные дни.

<b>Смотреть</b>
/stats — сводка: средние, разброс, время суток, динамика
/chart — графики: давление, сон, шаги, вес, пульс покоя
/last — последние 10 измерений (с кнопками удаления)
/undo — удалить последнее
/del <code>42</code> — удалить по номеру

<b>Напоминания</b>
/remind <code>08:00</code> — напоминать каждый день в это время
/remind <code>21:00</code> — можно несколько раз в день
/reminders — список, выключить, настроить
Если ты уже мерил давление за последние полтора часа, напоминание не придёт —
это можно отключить в /reminders.

<b>Для врача</b>
/export — PDF со сводкой, графиком и таблицей всех измерений
   или CSV, если врач просит таблицу
Порог «повышенного» дома ниже, чем в кабинете врача: гипертонией считают
средние значения от 135/85 (в кабинете — от 140/90). Поэтому цель по умолчанию 135/85.

<b>Настройки</b>
/target <code>130/80</code> — свои целевые значения (их назовёт врач)
/tz <code>Europe/Moscow</code> — часовой пояс
/about — о шкале и об ограничениях

<b>Как мерить, чтобы цифры были честными</b>
Сидя, спина опирается, ноги на полу, 5 минут покоя, манжета на уровне сердца,
не сразу после кофе, курения и нагрузки. Лучше два замера с интервалом в минуту."""

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
    await message.answer(START)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
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
