"""Клавиатуры раздела «Деньги»."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import Category, Transaction
from .stats import PERIODS


def transaction_actions(transaction: Transaction) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Категория", callback_data=f"pickcat:{transaction.id}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"delop:{transaction.id}"
                ),
            ],
            [InlineKeyboardButton(text="✅ Ок", callback_data="ok")],
        ]
    )


def category_picker(
    transaction_id: int, categories: Sequence[Category]
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(
            text=category.title, callback_data=f"setcat:{transaction_id}:{category.id}"
        )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"opback:{transaction_id}")
    )
    return builder.as_markup()


def money_periods(active: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, title in PERIODS:
        mark = "· " if key == active else ""
        builder.button(text=f"{mark}{title}", callback_data=f"mrep:{key}")
    builder.adjust(4)
    return builder.as_markup()


def balance_actions() -> InlineKeyboardMarkup:
    """Из баланса — в подробную сводку: два числа редко отвечают на всё."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Куда ушло и откуда пришло", callback_data="do:money:stats"
                )
            ]
        ]
    )


def delete_buttons(transactions: Sequence[Transaction]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for transaction in transactions:
        builder.button(text=f"🗑 #{transaction.id}", callback_data=f"delop:{transaction.id}")
    builder.adjust(3)
    return builder.as_markup()
