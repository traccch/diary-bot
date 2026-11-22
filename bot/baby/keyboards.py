"""Кнопки раздела «Малыш»."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def menu(sleeping: bool, is_owner: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if sleeping:
        builder.button(text="🌞 Проснулся", callback_data="baby:wake")
    else:
        builder.button(text="😴 Уснул", callback_data="baby:sleep")
    builder.button(text="📊 Сон сегодня", callback_data="baby:day")
    builder.button(text="📅 Неделя сна", callback_data="baby:week")
    builder.button(text="🌤 Погода и одежда", callback_data="baby:weather")
    builder.button(text="📏 Рост и вес", callback_data="baby:growth")
    builder.button(text="✍️ Сон задним числом", callback_data="baby:past")
    builder.button(text="↩️ Отменить последнее", callback_data="baby:undo")
    builder.button(text="⚙️ Настройки", callback_data="baby:settings")
    builder.adjust(1, 2, 2, 2, 1)
    if is_owner:
        builder.row(InlineKeyboardButton(text="← Главное меню", callback_data="do:home"))
    return builder.as_markup()


def after_action(sleeping: bool) -> InlineKeyboardMarkup:
    first = (
        InlineKeyboardButton(text="🌞 Проснулся", callback_data="baby:wake")
        if sleeping
        else InlineKeyboardButton(text="😴 Уснул", callback_data="baby:sleep")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[[first, InlineKeyboardButton(text="👶 Меню", callback_data="baby:menu")]]
    )


def back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="👶 Меню", callback_data="baby:menu")]]
    )


def sex_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👦 Мальчик", callback_data="baby:sex:m"),
                InlineKeyboardButton(text="👧 Девочка", callback_data="baby:sex:f"),
            ]
        ]
    )


def setup_start() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="👶 Завести", callback_data="baby:setup")]]
    )


def growth_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✍️ Записать замеры", callback_data="baby:measure"),
                InlineKeyboardButton(text="📈 График", callback_data="baby:growthchart"),
            ],
            [InlineKeyboardButton(text="👶 Меню", callback_data="baby:menu")],
        ]
    )


def settings(
    is_owner: bool, weather_on: bool, members: Sequence[tuple[int, str, str]]
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🏙 Город для погоды", callback_data="baby:city")
    builder.button(
        text="🌤 Погода утром: вкл" if weather_on else "🌤 Погода утром: выкл",
        callback_data="baby:wtoggle",
    )
    if is_owner:
        builder.button(text="👩 Пригласить жену", callback_data="baby:invite")
        for user_id, _, label in members:
            builder.button(text=f"🚫 Убрать доступ: {label}", callback_data=f"baby:kick:{user_id}")
    builder.button(text="👶 Меню", callback_data="baby:menu")
    builder.adjust(1)
    return builder.as_markup()
