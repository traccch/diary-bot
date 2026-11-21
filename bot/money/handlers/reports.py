"""Сводка по деньгам: /stats с переключением периода и /balance."""

from __future__ import annotations

import datetime as dt
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ...db import Database, UserSettings
from .. import charts
from ..db import EXPENSE
from ..keyboards import balance_actions, money_chart_periods, money_periods
from ..stats import VALID_PERIODS, balance_text, build_report, period_range

router = Router(name="money-reports")
logger = logging.getLogger(__name__)

NO_CHARTS = (
    "Графики рисует библиотека matplotlib, а она не установлена. "
    "Сводка цифрами на месте: /stats"
)

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
    await message.answer(await balance_text(db, user, today), reply_markup=balance_actions())


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


async def cmd_chart(
    message: Message,
    db: Database,
    user: UserSettings,
    today: dt.date,
    period: str = DEFAULT_PERIOD,
) -> None:
    """Картинка трат: по дням (или месяцам) и по категориям."""
    if not charts.available():
        await message.answer(NO_CHARTS)
        return

    start, end, title = period_range(period, today)
    if period == "all":
        start = await db.first_transaction_date(user.user_id) or today

    categories = await db.totals_by_category(
        user.user_id, start, end, EXPENSE, transfers=False
    )
    if not categories:
        await message.answer(
            "За этот период трат нет — рисовать нечего.",
            reply_markup=money_chart_periods(period),
        )
        return

    daily = await db.daily_totals(user.user_id, start, end, EXPENSE, transfers=False)
    try:
        image = charts.money_png(
            daily, categories, start, end, f"Расходы {title}", user.currency
        )
    except Exception:  # noqa: BLE001 - картинка не должна ронять диалог
        logger.exception("Не смог нарисовать график трат")
        await message.answer("Не получилось нарисовать график. Сводка на месте: /stats")
        return

    await message.answer_photo(
        BufferedInputFile(image, filename=f"money-{period}.png"),
        caption=f"📈 Расходы {title}",
        reply_markup=money_chart_periods(period),
    )


@router.callback_query(F.data.startswith("mchart:"))
async def cb_chart(
    callback: CallbackQuery, db: Database, user: UserSettings, today: dt.date
) -> None:
    period = callback.data.split(":", 1)[1]
    if period not in VALID_PERIODS:
        await callback.answer()
        return
    await callback.answer("Рисую…")
    if isinstance(callback.message, Message):
        await cmd_chart(callback.message, db, user, today, period)
