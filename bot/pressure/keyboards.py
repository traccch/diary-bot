"""Клавиатуры раздела «Давление»."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import Measurement
from .metrics import ALL_KINDS
from .stats import PERIODS

def measurement_actions(measurement: Measurement) -> InlineKeyboardMarkup:
    note_title = "✏️ Изменить" if measurement.note else "💬 Комментарий"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=note_title, callback_data=f"note:{measurement.id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{measurement.id}"),
            ],
            [InlineKeyboardButton(text="✅ Ок", callback_data="ok")],
        ]
    )


def delete_buttons(measurements: Sequence[Measurement]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for measurement in measurements:
        builder.button(text=f"🗑 #{measurement.id}", callback_data=f"del:{measurement.id}")
    builder.adjust(3)
    return builder.as_markup()


def period_switch(active: str, prefix: str = "stats") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, _, title in PERIODS:
        mark = "· " if key == active else ""
        builder.button(text=f"{mark}{title}", callback_data=f"{prefix}:{key}")
    builder.adjust(4)
    if prefix == "stats":
        builder.row(
            InlineKeyboardButton(text="📈 График", callback_data=f"chart:bp:{active}")
        )
    return builder.as_markup()


def chart_switch(
    active_kind: str, period: str, available: Sequence[str]
) -> InlineKeyboardMarkup:
    """Переключатель графиков: давление и те показатели, по которым есть данные."""
    builder = InlineKeyboardBuilder()
    buttons = [("bp", "🩺 Давление")]
    buttons += [
        (kind.key, f"{kind.icon} {kind.title}")
        for kind in ALL_KINDS
        if kind.key in available
    ]
    for key, title in buttons:
        mark = "· " if key == active_kind else ""
        builder.button(text=f"{mark}{title}", callback_data=f"chart:{key}:{period}")
    builder.adjust(2)
    return builder.as_markup()


def export_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📄 PDF · 30 дней", callback_data="exp:pdf:month")
    builder.button(text="📄 PDF · 3 месяца", callback_data="exp:pdf:quarter")
    builder.button(text="📄 PDF · всё время", callback_data="exp:pdf:all")
    builder.button(text="📊 CSV · всё время", callback_data="exp:csv:all")
    builder.adjust(2)
    return builder.as_markup()


def skip_note() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без комментария", callback_data="note:skip")]
        ]
    )
