"""Общие инлайн-клавиатуры: разделы, напоминания, обновление."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import Reminder
from .prompts import Prompt
from .sections import SECTIONS


def update_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬇️ Обновить бота", callback_data="upd:apply")]
        ]
    )


def reminder_actions(topic: str = "pressure") -> InlineKeyboardMarkup:
    if topic == "english":
        # У английского нет «записать»: там сразу начинается сессия карточек.
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="▶️ Начать", callback_data="eng:more"),
                    InlineKeyboardButton(
                        text="⏱ Через 15 минут", callback_data="rem:snooze:english"
                    ),
                ]
            ]
        )
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



def health_prompt(prompt: Prompt) -> InlineKeyboardMarkup:
    """Готовые ответы на вопрос о самочувствии: нажал — записалось.

    Нижний ряд есть всегда: у веса кнопок нет вовсе (шаг в сто граммов ими не
    выбрать), да и в остальных случаях отказаться должно быть так же просто,
    как ответить — иначе вопрос превращается в обязательство.
    """
    builder = InlineKeyboardBuilder()
    for choice in prompt.choices:
        builder.button(
            text=choice.label, callback_data=f"hm:{prompt.kind}:{choice.value:g}"
        )
    builder.adjust(4)
    builder.row(
        InlineKeyboardButton(text="⏭ Не сегодня", callback_data="hm:skip"),
        InlineKeyboardButton(text="⏱ Позже", callback_data="rem:snooze:health"),
    )
    return builder.as_markup()


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
