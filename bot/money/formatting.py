"""Форматирование раздела «Деньги»: карточки операций и списки."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from ..formatting import esc, format_date, format_money
from .db import Transaction


def render_transaction(
    transaction: Transaction, currency: str, today: Optional[dt.date] = None
) -> str:
    """Карточка операции для ответа бота."""
    note = f" · {esc(transaction.note)}" if transaction.note else ""
    icon = "🟢 Доход" if transaction.is_income else "✅"
    when = format_date(transaction.happened_on, today)
    return (
        f"{icon} <b>{format_money(transaction.amount, currency)}</b> — "
        f"{esc(transaction.category_title)}{note}\n"
        f"<i>{when} · #{transaction.id}</i>"
    )


def render_line(
    transaction: Transaction, currency: str, today: Optional[dt.date] = None
) -> str:
    """Строка для списка последних операций."""
    note = f" · {esc(transaction.note)}" if transaction.note else ""
    sign = "＋" if transaction.is_income else "−"
    return (
        f"<code>#{transaction.id}</code> {format_date(transaction.happened_on, today)} — "
        f"{sign}<b>{format_money(transaction.amount, currency)}</b> "
        f"{esc(transaction.category_title)}{note}"
    )
