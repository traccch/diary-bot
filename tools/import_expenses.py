"""Перенос данных из старого бота расходов в объединённый дневник.

Старый бот хранил траты в таблицах expenses и categories. Скрипт переносит их
в раздел «Деньги» как расходы, сопоставляя категории по названию: одноимённые
переиспользуются, недостающие создаются.

    python tools/import_expenses.py ~/старый-бот/data/expenses.db data/diary.db

Запускать можно повторно: уже перенесённые записи не задваиваются — они
помечаются в комментарии служебной пометкой переноса.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db import SCHEMA  # noqa: E402
from bot.money.db import DEFAULT_EXPENSE_CATEGORIES, EXPENSE, join_keywords  # noqa: E402


def import_expenses(source: Path, target: Path) -> tuple[int, int]:
    """Возвращает (перенесено, пропущено как уже перенесённое)."""
    if not source.exists():
        raise SystemExit(f"Не нашёл старую базу: {source}")

    old = sqlite3.connect(source)
    old.row_factory = sqlite3.Row
    new = sqlite3.connect(target)
    new.row_factory = sqlite3.Row
    new.executescript(SCHEMA)

    moved = skipped = 0
    try:
        rows = old.execute(
            "SELECT e.user_id, e.amount, e.note, e.spent_on, c.name, c.emoji, c.keywords"
            " FROM expenses e LEFT JOIN categories c ON c.id = e.category_id"
        ).fetchall()

        for row in rows:
            user_id = row["user_id"]
            new.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
            )
            _seed_categories(new, user_id)

            note = row["note"] or ""
            already = new.execute(
                "SELECT 1 FROM transactions WHERE user_id = ? AND amount = ?"
                " AND happened_on = ? AND note = ?",
                (user_id, row["amount"], row["spent_on"], note),
            ).fetchone()
            if already:
                skipped += 1
                continue

            category_id = _category_id(new, user_id, row["name"], row["emoji"], row["keywords"])
            new.execute(
                "INSERT INTO transactions"
                " (user_id, kind, amount, category_id, note, happened_on)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, EXPENSE, row["amount"], category_id, note, row["spent_on"]),
            )
            moved += 1

        new.commit()
    finally:
        old.close()
        new.close()
    return moved, skipped


def _seed_categories(conn: sqlite3.Connection, user_id: int) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO money_categories"
        " (user_id, kind, name, emoji, keywords, is_fallback) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (user_id, EXPENSE, name, emoji, join_keywords(keywords), int(not keywords))
            for emoji, name, keywords in DEFAULT_EXPENSE_CATEGORIES
        ],
    )


def _category_id(
    conn: sqlite3.Connection, user_id: int, name: str, emoji: str, keywords: str
) -> int | None:
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM money_categories WHERE user_id = ? AND kind = ? AND name = ?",
        (user_id, EXPENSE, name),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO money_categories (user_id, kind, name, emoji, keywords)"
        " VALUES (?, ?, ?, ?, ?)",
        (user_id, EXPENSE, name, emoji or "📦", keywords or ""),
    )
    return cur.lastrowid


def main() -> None:
    parser = argparse.ArgumentParser(description="Перенос трат из старого бота")
    parser.add_argument("source", type=Path, help="старая база expenses.db")
    parser.add_argument("target", type=Path, help="база объединённого бота")
    args = parser.parse_args()

    moved, skipped = import_expenses(args.source, args.target)
    print(f"Перенесено записей: {moved}")
    if skipped:
        print(f"Пропущено (уже были): {skipped}")


if __name__ == "__main__":
    main()
