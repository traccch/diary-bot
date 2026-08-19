"""Перенос измерений давления из текста, в том числе из фотографий тонометра.

Читать цифры с экрана тонометра автоматически — затея неблагодарная:
семисегментные цифры, блики и наклон камеры сбивают распознавание, и половину
всё равно пришлось бы перепроверять. Зато вторую половину работы — дату и
время каждого снимка — можно не набирать вообще: они лежат в самом файле.

Поэтому работа в два шага.

1. Заготовка из папки с фото. Дата и время берутся из EXIF:

       python tools/import_pressure.py --photos ~/фото --template замеры.txt

   Получится файл, где остаётся дописать цифры:

       12.08.2026 09:14              # IMG_0231.JPG
       12.08.2026 21:03              # IMG_0232.JPG

   Открываешь, глядя на снимки дописываешь давление в любое место строки:

       12.08.2026 09:14 140/90 72    # IMG_0231.JPG

2. Импорт заполненного файла:

       python tools/import_pressure.py замеры.txt data/diary.db --dry-run
       python tools/import_pressure.py замеры.txt data/diary.db

Строки без давления просто пропускаются — можно заполнять частями. Повторный
запуск не задваивает: замер с тем же временем считается уже перенесённым.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db import Database  # noqa: E402
from bot.formatting import plural  # noqa: E402
from bot.pressure.classify import classify  # noqa: E402
from bot.pressure.parsing import ParseError, parse_entry  # noqa: E402

#: Расширения, из которых Pillow умеет читать EXIF.
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
#: Их EXIF Pillow без дополнительных пакетов не читает.
UNSUPPORTED_SUFFIXES = {".heic", ".heif"}

EXIF_DATE_TAKEN = 36867  # DateTimeOriginal
EXIF_DATE = 306  # DateTime
EXIF_IFD = 0x8769

TEMPLATE_HEADER = """# Заготовка из фотографий: дата и время уже проставлены.
# Допиши давление в любое место строки, например «140/90 72», можно с
# комментарием: «140/90 72 после прогулки». Всё после # — примечание,
# в базу оно не попадёт. Строки без давления пропускаются.
"""


@dataclass
class PhotoRow:
    path: Path
    taken_at: dt.datetime
    from_exif: bool


@dataclass
class Row:
    line_no: int
    systolic: int
    diastolic: int
    pulse: Optional[int]
    measured_at: dt.datetime
    note: str


# ------------------------------------------------------------------- фото


def photo_taken_at(path: Path) -> tuple[dt.datetime, bool]:
    """Когда снят кадр. Второе значение — правда ли это EXIF, а не время файла."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow приезжает с matplotlib
        return dt.datetime.fromtimestamp(path.stat().st_mtime), False

    try:
        with Image.open(path) as image:
            exif = image.getexif()
            raw = exif.get_ifd(EXIF_IFD).get(EXIF_DATE_TAKEN) or exif.get(EXIF_DATE)
    except Exception:  # noqa: BLE001 - битый файл не должен ронять разбор папки
        raw = None

    if raw:
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return dt.datetime.strptime(str(raw).strip(), fmt), True
            except ValueError:
                continue
    return dt.datetime.fromtimestamp(path.stat().st_mtime), False


def scan_photos(folder: Path) -> tuple[list[PhotoRow], list[Path]]:
    """Возвращает (снимки по времени, файлы, у которых EXIF не прочитать)."""
    rows: list[PhotoRow] = []
    unsupported: list[Path] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in UNSUPPORTED_SUFFIXES:
            unsupported.append(path)
            continue
        if suffix not in PHOTO_SUFFIXES:
            continue
        taken_at, from_exif = photo_taken_at(path)
        rows.append(PhotoRow(path=path, taken_at=taken_at, from_exif=from_exif))

    rows.sort(key=lambda item: item.taken_at)
    return rows, unsupported


def write_template(rows: list[PhotoRow], target: Path) -> None:
    width = max((len(row.path.name) for row in rows), default=0)
    lines = [TEMPLATE_HEADER]
    for row in rows:
        mark = "" if row.from_exif else "  (время файла, не съёмки)"
        lines.append(
            f"{row.taken_at:%d.%m.%Y %H:%M}"
            f"{' ' * 18}# {row.path.name.ljust(width)}{mark}"
        )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ импорт


def read_rows(path: Path) -> tuple[list[Row], list[tuple[int, str]]]:
    """Возвращает (разобранные измерения, строки с цифрами, которые не понял)."""
    rows: list[Row] = []
    failed: list[tuple[int, str]] = []

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        try:
            entry = parse_entry(line)
        except ParseError:
            failed.append((line_no, raw.strip()))
            continue

        if entry.measurement is None:
            if any(char.isdigit() for char in line.replace(".", "").replace(":", "")):
                # дата есть, давления нет — строку просто не заполнили
                if not _only_date(line):
                    failed.append((line_no, raw.strip()))
            continue

        parsed = entry.measurement
        rows.append(
            Row(
                line_no=line_no,
                systolic=parsed.systolic,
                diastolic=parsed.diastolic,
                pulse=parsed.pulse,
                measured_at=parsed.measured_at,
                note=parsed.note,
            )
        )
    return rows, failed


def _only_date(line: str) -> bool:
    """Строка заготовки, в которую ещё не вписали давление."""
    stripped = line.replace(".", " ").replace(":", " ").split()
    return all(part.isdigit() for part in stripped) and len(stripped) <= 5


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
            existing = await db.measurements_between(
                user_id, row.measured_at, row.measured_at
            )
            if existing:
                skipped += 1
                continue
            await db.add_measurement(
                user_id, row.systolic, row.diastolic, row.pulse, row.measured_at, row.note
            )
            added += 1
        return added, skipped
    finally:
        await db.close()


def report(rows: list[Row], failed: list[tuple[int, str]]) -> None:
    print(f"Разобрано измерений: {len(rows)}")
    if rows:
        print(
            f"  период: {min(r.measured_at for r in rows):%d.%m.%Y} — "
            f"{max(r.measured_at for r in rows):%d.%m.%Y}"
        )
        counted: dict[str, int] = {}
        for row in rows:
            grade = classify(row.systolic, row.diastolic)
            counted[grade.title] = counted.get(grade.title, 0) + 1
        for title, count in sorted(counted.items(), key=lambda item: -item[1]):
            print(f"  {title:<34} {count}")

    if failed:
        word = plural(len(failed), "строку", "строки", "строк")
        print(f"\nНе понял {len(failed)} {word}:")
        for line_no, text in failed[:20]:
            print(f"  строка {line_no}: {text}")
        if len(failed) > 20:
            print(f"  …и ещё {len(failed) - 20}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Перенос измерений давления из текста или заготовки по фото"
    )
    parser.add_argument("source", type=Path, nargs="?", help="заполненный текстовый файл")
    parser.add_argument("database", type=Path, nargs="?", help="база, например data/diary.db")
    parser.add_argument("--photos", type=Path, help="папка с фотографиями тонометра")
    parser.add_argument("--template", type=Path, help="куда записать заготовку")
    parser.add_argument("--user", type=int, default=None, help="telegram id владельца")
    parser.add_argument("--dry-run", action="store_true", help="только показать разбор")
    args = parser.parse_args()

    if args.photos:
        if not args.template:
            raise SystemExit("Укажи, куда записать заготовку: --template замеры.txt")
        if not args.photos.is_dir():
            raise SystemExit(f"Не нашёл папку: {args.photos}")

        rows, unsupported = scan_photos(args.photos)
        if not rows and not unsupported:
            raise SystemExit("В папке нет снимков, которые я умею читать.")

        write_template(rows, args.template)
        guessed = sum(1 for row in rows if not row.from_exif)
        print(f"Снимков: {len(rows)} → {args.template}")
        if rows:
            print(f"  период: {rows[0].taken_at:%d.%m.%Y} — {rows[-1].taken_at:%d.%m.%Y}")
        if guessed:
            print(
                f"  у {guessed} нет даты съёмки в EXIF — взял время файла, "
                "проверь эти строки"
            )
        if unsupported:
            word = plural(len(unsupported), "файл", "файла", "файлов")
            print(
                f"  пропустил {len(unsupported)} {word} HEIC/HEIF: их EXIF я не читаю. "
                "Сконвертируй в JPG или впиши дату руками."
            )
        print("\nТеперь открой файл и допиши давление в строки, потом:")
        print(f"  python tools/import_pressure.py {args.template} data/diary.db --dry-run")
        return

    if not args.source:
        raise SystemExit("Укажи файл с измерениями или --photos с папкой снимков.")
    if not args.source.exists():
        raise SystemExit(f"Не нашёл файл: {args.source}")

    rows, failed = read_rows(args.source)
    report(rows, failed)

    if args.dry_run:
        print("\nПробный запуск: в базу ничего не записано.")
        for row in rows[:15]:
            pulse = f" ♥{row.pulse}" if row.pulse else ""
            note = f"  {row.note}" if row.note else ""
            print(f"  {row.measured_at:%d.%m.%Y %H:%M}  {row.systolic}/{row.diastolic}{pulse}{note}")
        if len(rows) > 15:
            print(f"  …и ещё {len(rows) - 15}")
        return

    if not rows:
        print("\nЗаписывать нечего.")
        return
    if not args.database:
        raise SystemExit("Укажи базу вторым аргументом, например data/diary.db")

    added, skipped = asyncio.run(store(rows, args.database, args.user))
    print(f"\nЗаписано: {added}")
    if skipped:
        print(f"Пропущено (замер за это время уже есть): {skipped}")


if __name__ == "__main__":
    main()
