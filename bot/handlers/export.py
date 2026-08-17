"""Выгрузка для врача: PDF со сводкой и графиком либо CSV."""

from __future__ import annotations

import datetime as dt
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from .. import charts
from ..db import Database, UserSettings
from ..export import csv_bytes, text_report
from ..formatting import format_period, measurements_word
from ..keyboards import export_menu
from ..stats import (
    PERIOD_TITLES,
    collect_health,
    health_lines_plain,
    period_range,
    summarize,
)

router = Router(name="export")
logger = logging.getLogger(__name__)

MENU = (
    "📤 <b>Выгрузка</b>\n\n"
    "PDF — сводка, график и таблица всех измерений: то, что удобно распечатать "
    "и отдать кардиологу.\n"
    "CSV — та же таблица для Excel.\n\n"
    "Что прислать?"
)


@router.message(Command("export", "doctor", "pdf"))
async def cmd_export(message: Message, db: Database, user: UserSettings) -> None:
    if await db.count_measurements(user.user_id) == 0:
        await message.answer("Выгружать нечего — дневник пуст.")
        return
    await message.answer(MENU, reply_markup=export_menu())


@router.callback_query(F.data.startswith("exp:"))
async def cb_export(
    callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    _, kind, period = callback.data.split(":")
    start, end = period_range(period, now)
    measurements = await db.measurements_between(user.user_id, start, end)

    if not measurements:
        await callback.answer("За этот период записей нет", show_alert=True)
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    await callback.answer("Готовлю файл…")
    summary = summarize(measurements, user.target_sys, user.target_dia)
    assert summary is not None
    health = health_lines_plain(
        *await collect_health(db, user, measurements, start, end)
    )
    stamp = f"{summary.start:%Y%m%d}-{summary.end:%Y%m%d}"
    caption = (
        f"🩺 {format_period(summary.start, summary.end)} · "
        f"{summary.count} {measurements_word(summary.count)}\n"
        f"Среднее {summary.avg_sys}/{summary.avg_dia}"
        + (f" · ♥ {summary.avg_pulse}" if summary.avg_pulse else "")
    )

    if kind == "csv":
        await callback.message.answer_document(
            BufferedInputFile(csv_bytes(measurements), filename=f"pressure-{stamp}.csv"),
            caption=caption,
        )
        return

    if charts.available():
        try:
            payload = charts.doctor_pdf(measurements, summary, user, now, health=health)
            await callback.message.answer_document(
                BufferedInputFile(payload, filename=f"pressure-{stamp}.pdf"),
                caption=f"{caption}\n<i>{PERIOD_TITLES.get(period, '')}</i>",
            )
            return
        except Exception:  # noqa: BLE001 - падаем в текстовый отчёт, а не в чат
            logger.exception("Не смог собрать PDF")

    await callback.message.answer_document(
        BufferedInputFile(
            text_report(measurements, summary, user, now, health),
            filename=f"pressure-{stamp}.txt",
        ),
        caption=f"{caption}\n<i>PDF собрать не вышло — прислал текстом.</i>",
    )
