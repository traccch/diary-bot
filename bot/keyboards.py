"""Общие инлайн-клавиатуры: разделы, напоминания, обновление."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import Reminder
from .sections import SECTIONS


def update_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬇️ Обновить бота", callback_data="upd:apply")]
        ]
    )


def reminder_actions(topic: str = "pressure") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✍️ Записать", callback_data=f"rem:write:{topic}"),
                InlineKeyboardButton(
                    text="⏱ Через 15 минут", callback_data=f"rem:snooze:{topic}"
                ),
            ]
        ]
    )



def reminder_list(reminders: Sequence[Reminder], skip_if_measured: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for reminder in reminders:
        builder.button(
            text=f"🗑 {reminder.label}",
            callback_data=f"remdel:{reminder.topic}:{reminder.label}",
        )
    builder.adjust(3)
    toggle = "✅ Не дублировать" if skip_if_measured else "☑️ Не дублировать"
    builder.row(InlineKeyboardButton(text=toggle, callback_data="remskip"))
    return builder.as_markup()


def section_menu(active: str) -> InlineKeyboardMarkup:
    """Меню разделов: что открыть — давление или деньги."""
    builder = InlineKeyboardBuilder()
    for section in SECTIONS:
        mark = "· " if section.key == active else ""
        builder.button(text=f"{mark}{section.label}", callback_data=f"go:{section.key}")
    builder.adjust(2)
    return builder.as_markup()
