"""Клавиатуры раздела «Английский»."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def answer_options(options: Sequence[str]) -> InlineKeyboardMarkup:
    """Варианты ответа: по одному в строке — иначе длинные фразы обрезаются."""
    builder = InlineKeyboardBuilder()
    for index, option in enumerate(options):
        builder.button(text=option, callback_data=f"eng:a:{index}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🤷 Не знаю", callback_data="eng:idk"))
    return builder.as_markup()


def after_card() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="▶️ Дальше", callback_data="eng:next")]]
    )


def after_session(has_quest: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔁 Ещё карточки", callback_data="eng:more")
    if has_quest:
        builder.button(text="🗺 Квест", callback_data="eng:quest")
    builder.button(text="📈 Прогресс", callback_data="eng:stats")
    builder.adjust(2)
    return builder.as_markup()


def english_menu(due: int, has_quest: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"▶️ Карточки ({due})" if due else "▶️ Карточки", callback_data="eng:more"
    )
    if has_quest:
        builder.button(text="🗺 Квест", callback_data="eng:quest")
    builder.button(text="📈 Прогресс", callback_data="eng:stats")
    builder.adjust(2)
    return builder.as_markup()


def quest_start(quest_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Прочитал, к вопросам", callback_data=f"eq:go:{quest_id}")]
        ]
    )


def quest_options(options: Sequence[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, option in enumerate(options):
        builder.button(text=option, callback_data=f"eq:a:{index}")
    builder.adjust(1)
    return builder.as_markup()


def quest_next() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="▶️ Дальше", callback_data="eq:next")]]
    )


def reminder_start() -> InlineKeyboardMarkup:
    """Кнопка под напоминанием: начать прямо из уведомления."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="▶️ Начать", callback_data="eng:more"),
                InlineKeyboardButton(text="⏱ Через 15 минут", callback_data="rem:snooze:english"),
            ]
        ]
    )
