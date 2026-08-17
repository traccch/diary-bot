"""Сводка /stats и график /chart."""

from __future__ import annotations

import datetime as dt
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from .. import charts
from ..db import Database, UserSettings
from ..formatting import measurements_word
from ..keyboards import period_switch
from ..stats import PERIOD_DAYS, PERIOD_TITLES, build_report, period_range

router = Router(name="reports")
logger = logging.getLogger(__name__)

DEFAULT_PERIOD = "month"

NO_CHARTS = (
    "Графики рисует библиотека matplotlib, а она не установлена.\n"
    "Поставь её командой <code>pip install matplotlib</code> и перезапусти бота — "
    "или пользуйся текстовой сводкой /stats, там всё то же самое."
)


async def send_chart(
    target: Message, db: Database, user: UserSettings, period: str, now: dt.datetime
) -> None:
    if not charts.available():
        await target.answer(NO_CHARTS)
        return

    start, end = period_range(period, now)
    measurements = await db.measurements_between(user.user_id, start, end)
    if len(measurements) < 2:
        await target.answer(
            "Для графика нужно хотя бы два измерения за период. "
            "Пришли ещё пару — <code>120/80 68</code>."
        )
        return

    title = f"Давление {PERIOD_TITLES.get(period, '')}".strip()
    try:
        image = charts.pressure_png(measurements, user, title)
    except Exception:  # noqa: BLE001 - картинка не должна ронять диалог
        logger.exception("Не смог нарисовать график")
        await target.answer("Не получилось нарисовать график. Сводка на месте: /stats")
        return

    await target.answer_photo(
        BufferedInputFile(image, filename=f"pressure-{period}.png"),
        caption=f"📈 {title} · {len(measurements)} {measurements_word(len(measurements))}",
    )


@router.message(Command("stats", "report"))
async def cmd_stats(
    message: Message, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    text = await build_report(db, user, DEFAULT_PERIOD, now)
    await message.answer(text, reply_markup=period_switch(DEFAULT_PERIOD))


@router.message(Command("chart", "graph"))
async def cmd_chart(
    message: Message, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    await send_chart(message, db, user, DEFAULT_PERIOD, now)


@router.callback_query(F.data.startswith("stats:"))
async def cb_stats(
    callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    period = callback.data.split(":", 1)[1]
    if period not in PERIOD_DAYS:
        await callback.answer()
        return

    text = await build_report(db, user, period, now)
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(text, reply_markup=period_switch(period))
    except TelegramBadRequest:
        pass  # текст не изменился — для Telegram это ошибка, для нас нет


@router.callback_query(F.data.startswith("chart:"))
async def cb_chart(
    callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    period = callback.data.split(":", 1)[1]
    if period not in PERIOD_DAYS:
        await callback.answer()
        return
    await callback.answer("Рисую…")
    if isinstance(callback.message, Message):
        await send_chart(callback.message, db, user, period, now)
