"""Сводка по деньгам: /stats с переключением периода и /balance."""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ...db import Database, UserSettings
from ..keyboards import money_periods
from ..stats import VALID_PERIODS, balance_text, build_report

router = Router(name="money-reports")

DEFAULT_PERIOD = "month"


async def cmd_stats(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    text = await build_report(db, user, DEFAULT_PERIOD, today)
    await message.answer(text, reply_markup=money_periods(DEFAULT_PERIOD))


@router.message(Command("balance"))
async def cmd_balance(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    await message.answer(await balance_text(db, user, today))


@router.callback_query(F.data.startswith("mrep:"))
async def cb_report(
    callback: CallbackQuery, db: Database, user: UserSettings, today: dt.date
) -> None:
    period = callback.data.split(":", 1)[1]
    if period not in VALID_PERIODS:
        await callback.answer()
        return

    text = await build_report(db, user, period, today)
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(text, reply_markup=money_periods(period))
    except TelegramBadRequest:
        pass  # текст не изменился — для Telegram это ошибка, для нас нет
