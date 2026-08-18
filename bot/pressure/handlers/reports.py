"""Сводка /stats и график /chart."""

from __future__ import annotations

import datetime as dt
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ...db import Database, UserSettings
from ...formatting import format_period, plural
from ..keyboards import chart_switch, period_switch
from .. import charts, metrics
from ..formatting import measurements_word
from ..stats import PERIOD_DAYS, PERIOD_TITLES, build_report, period_range

router = Router(name="reports")
logger = logging.getLogger(__name__)

DEFAULT_PERIOD = "month"

NO_CHARTS = (
    "Графики рисует библиотека matplotlib, а она не установлена.\n"
    "Поставь её командой <code>pip install matplotlib</code> и перезапусти бота — "
    "или пользуйся текстовой сводкой /stats, там всё то же самое."
)


async def _available_kinds(
    db: Database, user: UserSettings, start: dt.datetime, end: dt.datetime
) -> list[str]:
    """Показатели, по которым за период есть хотя бы два дня данных."""
    found = []
    for kind in metrics.ALL_KINDS:
        values = await db.metrics_between(user.user_id, kind.key, start.date(), end.date())
        if len(values) >= 2:
            found.append(kind.key)
    return found


async def send_chart(
    target: Message,
    db: Database,
    user: UserSettings,
    period: str,
    now: dt.datetime,
    what: str = "bp",
) -> None:
    if not charts.available():
        await target.answer(NO_CHARTS)
        return

    start, end = period_range(period, now)
    available = await _available_kinds(db, user, start, end)
    keyboard = chart_switch(what, period, available)
    period_title = PERIOD_TITLES.get(period, "")

    kind = metrics.kind_of(what)
    if kind is not None:
        values = await db.metrics_between(user.user_id, kind.key, start.date(), end.date())
        if len(values) < 2:
            await target.answer(
                f"По показателю «{kind.title}» за период меньше двух записей.",
                reply_markup=keyboard,
            )
            return
        subtitle = (
            f"{format_period(values[0].on_date, values[-1].on_date)} · {len(values)} "
            f"{plural(len(values), 'запись', 'записи', 'записей')}"
        )
        payload = _render(
            lambda: charts.metric_png(
                kind, [(item.on_date, item.value) for item in values], subtitle
            )
        )
        caption = f"{kind.icon} {kind.title} {period_title}"
        filename = f"{kind.key}-{period}.png"
    else:
        measurements = await db.measurements_between(user.user_id, start, end)
        if len(measurements) < 2:
            await target.answer(
                "Для графика нужно хотя бы два измерения за период. "
                "Пришли ещё пару — <code>120/80 68</code>.",
                reply_markup=keyboard,
            )
            return
        title = f"Давление {period_title}".strip()
        payload = _render(lambda: charts.pressure_png(measurements, user, title))
        caption = (
            f"📈 {title} · {len(measurements)} {measurements_word(len(measurements))}"
        )
        filename = f"pressure-{period}.png"

    if payload is None:
        await target.answer("Не получилось нарисовать график. Сводка на месте: /stats")
        return

    await target.answer_photo(
        BufferedInputFile(payload, filename=filename),
        caption=caption,
        reply_markup=keyboard,
    )


def _render(build) -> bytes | None:
    """Картинка не должна ронять диалог: не получилось — вернём None."""
    try:
        return build()
    except Exception:  # noqa: BLE001
        logger.exception("Не смог нарисовать график")
        return None


async def cmd_stats(
    message: Message, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    text = await build_report(db, user, DEFAULT_PERIOD, now)
    await message.answer(text, reply_markup=period_switch(DEFAULT_PERIOD))


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
    _, what, period = callback.data.split(":")
    if period not in PERIOD_DAYS or (what != "bp" and metrics.kind_of(what) is None):
        await callback.answer()
        return
    await callback.answer("Рисую…")
    if isinstance(callback.message, Message):
        await send_chart(callback.message, db, user, period, now, what)
