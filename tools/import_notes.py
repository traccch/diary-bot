"""Перенос денежных записей из обычных заметок в базу дневника.

Заметки обычно выглядят так: строка с датой, под ней операции со знаком.
Скрипт читает такой файл, раскладывает всё по датам и категориям и кладёт
в раздел «Деньги».

    python tools/import_notes.py заметки.txt data/diary.db --dry-run
    python tools/import_notes.py заметки.txt data/diary.db

Понимает примерно такое:

    12.08
    - 500 продукты
    -1200 бензин
    + 90000 зарплата

    2026-08-13
    350 кофе          ← без знака это расход

Строки, которые разобрать не вышло, не пропадают молча: в конце будет
список с номерами строк, чтобы можно было поправить и запустить снова.
Повторный запуск не задваивает — одинаковые записи за ту же дату пропускаются.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db import Database  # noqa: E402
from bot.formatting import MONTHS_GENITIVE, format_money, plural  # noqa: E402
from bot.money.db import EXPENSE, INCOME  # noqa: E402
from bot.money.parsing import ParseError, match_category, parse_transaction  # noqa: E402

WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота",
    "воскресенье", "пн", "вт", "ср", "чт", "пт", "сб", "вс",
)

_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DOTTED_DATE = re.compile(r"^(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\.?$")
_WORD_DATE = re.compile(r"^(\d{1,2})\s+([а-яё]+)\.?(?:\s+(\d{4}))?$", re.IGNORECASE)


@dataclass
class Row:
    line_no: int
    text: str
    kind: str
    amount: int
    note: str
    on_date: dt.date


def parse_date_line(line: str, default_year: int, today: dt.date) -> Optional[dt.date]:
    """Строка целиком из даты — «12.08», «2026-08-13», «12 августа»."""
    cleaned = line.strip().strip("—-–:").strip()
    if not cleaned or cleaned[0] in "+-−–":
        return None

    words = [word for word in cleaned.split() if word.lower().strip(",.") not in WEEKDAYS]
    cleaned = " ".join(words).strip(", ")
    if not cleaned:
        return None

    match = _ISO_DATE.match(cleaned)
    if match:
        return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    match = _DOTTED_DATE.match(cleaned)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year = int(match.group(3)) if match.group(3) else default_year
        if year < 100:
            year += 2000
        return _past_date(day, month, year, match.group(3) is None, today)

    match = _WORD_DATE.match(cleaned)
    if match:
        name = match.group(2).lower().replace("ё", "е")
        for index, month_name in enumerate(MONTHS_GENITIVE, start=1):
            if month_name.replace("ё", "е").startswith(name[:4]):
                year = int(match.group(3)) if match.group(3) else default_year
                return _past_date(
                    int(match.group(1)), index, year, match.group(3) is None, today
                )
    return None


def _safe_date(year: int, month: int, day: int) -> Optional[dt.date]:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _past_date(
    day: int, month: int, year: int, guessed_year: bool, today: dt.date
) -> Optional[dt.date]:
    """Без года дата не может быть в будущем — значит, это прошлый год."""
    parsed = _safe_date(year, month, day)
    if parsed is None:
        return None
    if guessed_year and parsed > today:
        return _safe_date(year - 1, month, day)
    return parsed


def read_rows(
    path: Path, default_year: int, today: dt.date
) -> tuple[list[Row], list[tuple[int, str]]]:
    """Возвращает (разобранные операции, непонятые строки)."""
    rows: list[Row] = []
    failed: list[tuple[int, str]] = []
    current = today

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        date = parse_date_line(line, default_year, today)
        if date is not None:
            current = date
            continue

        try:
            parsed = parse_transaction(line, current)
        except ParseError:
            failed.append((line_no, line))
            continue
        if parsed is None:
            failed.append((line_no, line))
            continue

        rows.append(
            Row(
                line_no=line_no,
                text=line,
                kind=parsed.kind,
                amount=parsed.amount,
                note=parsed.note,
                on_date=parsed.happened_on,
            )
        )
    return rows, failed


async def store(rows: list[Row], db_path: Path, user_id: Optional[int]) -> tuple[int, int]:
    db = Database(str(db_path), "Europe/Moscow")
    await db.connect()
    try:
        if user_id is None:
            user_id = await db.owner_id()
            if user_id is None:
                raise SystemExit(
                    "В базе ещё нет пользователей. Напиши боту /start, потом запусти снова "
                    "(или укажи --user с твоим id из @userinfobot)."
                )
        await db.ensure_user(user_id)

        added = skipped = 0
        for row in rows:
            existing = await db.transactions_between(
                user_id, row.on_date, row.on_date, row.kind
            )
            if any(
                item.amount == row.amount and item.note == row.note for item in existing
            ):
                skipped += 1
                continue

            categories = await db.list_categories(user_id, row.kind)
            category = match_category(row.note, categories) or await db.get_fallback_category(
                user_id, row.kind
            )
            await db.add_transaction(
                user_id, row.kind, row.amount, row.note, row.on_date,
                category.id if category else None,
            )
            added += 1
        return added, skipped
    finally:
        await db.close()


def report(rows: list[Row], failed: list[tuple[int, str]]) -> None:
    income = [row for row in rows if row.kind == INCOME]
    expense = [row for row in rows if row.kind == EXPENSE]
    print(f"Разобрано строк: {len(rows)}")
    if rows:
        print(f"  период: {min(r.on_date for r in rows)} — {max(r.on_date for r in rows)}")
    print(f"  доходы:  {len(income):>4} на {format_money(sum(r.amount for r in income))}")
    print(f"  расходы: {len(expense):>4} на {format_money(sum(r.amount for r in expense))}")

    if failed:
        word = plural(len(failed), "строку", "строки", "строк")
        print(f"\nНе понял {len(failed)} {word}:")
        for line_no, text in failed[:20]:
            print(f"  строка {line_no}: {text}")
        if len(failed) > 20:
            print(f"  …и ещё {len(failed) - 20}")
        print("Их можно поправить в файле и запустить снова — записанное не задвоится.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Перенос денежных записей из заметок")
    parser.add_argument("notes", type=Path, help="текстовый файл с заметками")
    parser.add_argument("database", type=Path, help="база бота, например data/diary.db")
    parser.add_argument("--user", type=int, default=None, help="telegram id владельца")
    parser.add_argument("--year", type=int, default=dt.date.today().year,
                        help="год для дат без года")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать, что получится, ничего не записывая")
    args = parser.parse_args()

    if not args.notes.exists():
        raise SystemExit(f"Не нашёл файл: {args.notes}")

    rows, failed = read_rows(args.notes, args.year, dt.date.today())
    report(rows, failed)

    if args.dry_run:
        print("\nПробный запуск: в базу ничего не записано.")
        for row in rows[:15]:
            sign = "+" if row.kind == INCOME else "−"
            print(f"  {row.on_date}  {sign}{format_money(row.amount):>14}  {row.note}")
        if len(rows) > 15:
            print(f"  …и ещё {len(rows) - 15}")
        return

    if not rows:
        print("\nЗаписывать нечего.")
        return

    added, skipped = asyncio.run(store(rows, args.database, args.user))
    print(f"\nЗаписано: {added}")
    if skipped:
        print(f"Пропущено (уже были): {skipped}")


if __name__ == "__main__":
    main()
