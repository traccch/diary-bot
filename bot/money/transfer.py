"""Загрузка операций файлом: то, что копилось в заметках, — одним махом.

Записи из блокнота, сообщений себе и бумажки на холодильнике всё равно
существуют, и переносить их по одной — работа на вечер. Здесь бот принимает
файл, показывает, что именно запишет, и записывает только после «да».

Формат нарочно простой, чтобы такой файл мог собрать и человек, и любой ИИ,
которому отдали фотографию блокнота: список операций, где знак суммы решает
всё — минус расход, плюс доход.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional, Sequence

from .db import EXPENSE, INCOME, Transaction

FORMAT = "diary-money-import"
VERSION = 1

#: Больше — это уже не перенос заметок, а чей-то чужой файл.
MAX_ROWS = 2000
MAX_AMOUNT_MINOR = 10**11

INSTRUCTIONS = (
    "Список операций для телеграм-бота-дневника. Суммы в рублях: минус — "
    "расход, плюс — доход. Даты — ГГГГ-ММ-ДД. Заметка — коротко, своими "
    "словами: по ней бот сам подберёт категорию."
)


class ImportError_(ValueError):
    """Файл не похож на список операций."""


@dataclass(frozen=True)
class Row:
    happened_on: dt.date
    kind: str
    amount: int  # в копейках, всегда положительное
    note: str

    @property
    def signed(self) -> int:
        return self.amount if self.kind == INCOME else -self.amount


@dataclass(frozen=True)
class Plan:
    rows: tuple[Row, ...]
    skipped: int
    duplicates: int

    def __bool__(self) -> bool:
        return bool(self.rows)

    @property
    def income(self) -> int:
        return sum(row.amount for row in self.rows if row.kind == INCOME)

    @property
    def expense(self) -> int:
        return sum(row.amount for row in self.rows if row.kind == EXPENSE)

    @property
    def period(self) -> Optional[tuple[dt.date, dt.date]]:
        if not self.rows:
            return None
        days = [row.happened_on for row in self.rows]
        return min(days), max(days)


def _to_minor(value: Any) -> Optional[int]:
    """Сумма в копейках. Знак сохраняем: он и означает доход или расход."""
    if isinstance(value, str):
        value = value.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    minor = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return minor if 0 < abs(minor) < MAX_AMOUNT_MINOR else None


def _to_date(value: Any, today: dt.date) -> Optional[dt.date]:
    if not value:
        return today
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def dump(rows: Sequence[Row], currency: str = "₽") -> bytes:
    """Тот же формат в обратную сторону — пригодится для переносов и проверки."""
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "_instructions": INSTRUCTIONS,
        "currency": currency,
        "transactions": [
            {
                "date": row.happened_on.isoformat(),
                "amount": round(row.signed / 100, 2),
                "note": row.note,
            }
            for row in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _key(day: dt.date, kind: str, amount: int, note: str) -> tuple:
    return (day, kind, amount, " ".join(note.lower().split()))


def parse(
    raw: bytes, today: dt.date, existing: Sequence[Transaction] = ()
) -> Plan:
    """Читает файл и отбирает то, чего в дневнике ещё нет."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ImportError_("Это не JSON — нужен файл со списком операций") from exc

    if isinstance(data, list):
        items = data  # список операций без обёртки — тоже понятно
    elif isinstance(data, dict):
        items = data.get("transactions") or data.get("operations")
    else:
        items = None

    if not isinstance(items, list):
        raise ImportError_("В файле нет списка transactions — похоже, это другой файл")
    if len(items) > MAX_ROWS:
        raise ImportError_(f"Слишком много записей: {len(items)}, максимум {MAX_ROWS}")

    seen = {
        _key(item.happened_on, item.kind, item.amount, item.note) for item in existing
    }

    rows: list[Row] = []
    skipped = 0
    duplicates = 0

    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue

        minor = _to_minor(item.get("amount"))
        day = _to_date(item.get("date"), today)
        note = str(item.get("note") or "").strip()[:200]
        if minor is None or day is None:
            skipped += 1
            continue

        # знак решает всё; поле kind принимаем, если оно есть и не противоречит
        kind = INCOME if minor > 0 else EXPENSE
        if str(item.get("kind") or "").lower() in {EXPENSE, INCOME}:
            kind = str(item["kind"]).lower()

        row = Row(day, kind, abs(minor), note)
        key = _key(row.happened_on, row.kind, row.amount, row.note)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        rows.append(row)

    return Plan(tuple(rows), skipped, duplicates)
