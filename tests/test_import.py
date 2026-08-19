"""Переносы: заметки с деньгами, измерения из текста и заготовка по фото."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db import Database
from bot.money.db import EXPENSE, INCOME
from tools import import_notes, import_pressure

TODAY = dt.date(2026, 8, 20)


class ParseDateLineTest(unittest.TestCase):
    def parse(self, line: str):
        return import_notes.parse_date_line(line, 2026, TODAY)

    def test_formats(self):
        cases = {
            "12.08": dt.date(2026, 8, 12),
            "12.08.2026": dt.date(2026, 8, 12),
            "12/08": dt.date(2026, 8, 12),
            "2026-08-12": dt.date(2026, 8, 12),
            "12 августа": dt.date(2026, 8, 12),
            "12 августа 2025": dt.date(2025, 8, 12),
            "среда, 12 августа": dt.date(2026, 8, 12),
            "пн 12.08": dt.date(2026, 8, 12),
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(self.parse(line), expected)

    def test_future_date_without_year_is_last_year(self):
        self.assertEqual(self.parse("25.12"), dt.date(2025, 12, 25))

    def test_operations_are_not_dates(self):
        for line in ("- 500 продукты", "+90000 зарплата", "350 кофе", "", "просто текст"):
            with self.subTest(line=line):
                self.assertIsNone(self.parse(line))

    def test_nonsense_dates_rejected(self):
        self.assertIsNone(self.parse("45.99"))


class ReadNotesTest(unittest.TestCase):
    def read(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            path.write_text(text, encoding="utf-8")
            return import_notes.read_rows(path, 2026, TODAY)

    def test_dates_apply_to_following_lines(self):
        rows, failed = self.read(
            "12.08\n- 500 продукты\n+90000 зарплата\n\n13.08\n350 кофе\n"
        )
        self.assertEqual(failed, [])
        self.assertEqual([row.on_date.day for row in rows], [12, 12, 13])
        self.assertEqual([row.kind for row in rows], [EXPENSE, INCOME, EXPENSE])
        self.assertEqual([row.amount for row in rows], [50000, 9_000_000, 35000])

    def test_no_sign_means_expense(self):
        rows, _ = self.read("12.08\n350 кофе\n")
        self.assertEqual(rows[0].kind, EXPENSE)

    def test_unparsed_lines_are_reported_not_dropped(self):
        rows, failed = self.read("12.08\n- 500 продукты\nчто-то забыл\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(failed, [(3, "что-то забыл")])

    def test_comments_and_blanks_are_skipped(self):
        rows, failed = self.read("# заметка\n\n12.08\n- 500\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(failed, [])

    def test_inline_date_wins(self):
        rows, _ = self.read("12.08\n- 500 продукты 05.08\n")
        self.assertEqual(rows[0].on_date, dt.date(2026, 8, 5))

    def test_thousands_shorthand(self):
        rows, _ = self.read("12.08\n-45к аренда\n")
        self.assertEqual(rows[0].amount, 4_500_000)


class StoreNotesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "diary.db"
        db = Database(str(self.path), "Europe/Moscow")
        await db.connect()
        await db.ensure_user(777)
        await db.close()

    async def asyncTearDown(self):
        self._tmp.cleanup()

    def rows(self):
        source = Path(self._tmp.name) / "notes.txt"
        source.write_text("12.08\n- 500 продукты\n+90000 зарплата\n", encoding="utf-8")
        return import_notes.read_rows(source, 2026, TODAY)[0]

    async def test_writes_and_assigns_categories(self):
        added, skipped = await import_notes.store(self.rows(), self.path, None)
        self.assertEqual((added, skipped), (2, 0))

        db = Database(str(self.path), "Europe/Moscow")
        await db.connect()
        stored = await db.last_transactions(777)
        names = {item.category_name for item in stored}
        self.assertIn("Продукты", names)
        self.assertIn("Зарплата", names)
        await db.close()

    async def test_second_run_does_not_duplicate(self):
        await import_notes.store(self.rows(), self.path, None)
        added, skipped = await import_notes.store(self.rows(), self.path, None)
        self.assertEqual((added, skipped), (0, 2))

    async def test_empty_database_explains_itself(self):
        empty = Path(self._tmp.name) / "empty.db"
        with self.assertRaises(SystemExit) as caught:
            await import_notes.store(self.rows(), empty, None)
        self.assertIn("/start", str(caught.exception))


class ReadPressureTest(unittest.TestCase):
    def read(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "замеры.txt"
            path.write_text(text, encoding="utf-8")
            return import_pressure.read_rows(path)

    def test_filled_template(self):
        rows, failed = self.read(
            "# шапка\n"
            "12.08.2026 09:14 140/90 72        # IMG_1.JPG\n"
            "12.08.2026 21:03 128/82 68 после прогулки  # IMG_2.JPG\n"
        )
        self.assertEqual(failed, [])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].measured_at, dt.datetime(2026, 8, 12, 9, 14))
        self.assertEqual((rows[0].systolic, rows[0].diastolic, rows[0].pulse), (140, 90, 72))
        self.assertEqual(rows[1].note, "после прогулки")

    def test_unfilled_lines_are_skipped_silently(self):
        rows, failed = self.read("12.08.2026 09:14        # IMG_1.JPG\n")
        self.assertEqual(rows, [])
        self.assertEqual(failed, [])

    def test_filename_never_leaks_into_the_note(self):
        rows, _ = self.read("12.08.2026 09:14 140/90   # IMG_1.JPG\n")
        self.assertEqual(rows[0].note, "")

    def test_broken_numbers_are_reported(self):
        rows, failed = self.read("12.08.2026 09:14 80/120   # IMG_1.JPG\n")
        self.assertEqual(rows, [])
        self.assertEqual(len(failed), 1)


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow не установлен")
class PhotoTemplateTest(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name) / "photos"
        self.folder.mkdir()

        base = dt.datetime(2026, 8, 12, 9, 14)
        for index in range(3):
            image = Image.new("RGB", (8, 8))
            exif = image.getexif()
            exif.get_ifd(import_pressure.EXIF_IFD)[import_pressure.EXIF_DATE_TAKEN] = (
                (base + dt.timedelta(days=index)).strftime("%Y:%m:%d %H:%M:%S")
            )
            image.save(self.folder / f"IMG_{index}.JPG", exif=exif)

        Image.new("RGB", (8, 8)).save(self.folder / "no_exif.jpg")
        (self.folder / "phone.HEIC").write_bytes(b"heic")
        (self.folder / "readme.txt").write_text("не фото", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_scan_reads_exif_and_sorts(self):
        rows, unsupported = import_pressure.scan_photos(self.folder)
        with_exif = [row for row in rows if row.from_exif]
        self.assertEqual(len(with_exif), 3)
        self.assertEqual(with_exif[0].taken_at, dt.datetime(2026, 8, 12, 9, 14))
        self.assertEqual([path.name for path in unsupported], ["phone.HEIC"])
        self.assertTrue(all(rows[i].taken_at <= rows[i + 1].taken_at for i in range(len(rows) - 1)))

    def test_photo_without_exif_is_marked(self):
        rows, _ = import_pressure.scan_photos(self.folder)
        guessed = [row for row in rows if not row.from_exif]
        self.assertEqual([row.path.name for row in guessed], ["no_exif.jpg"])

    def test_template_round_trip(self):
        rows, _ = import_pressure.scan_photos(self.folder)
        target = Path(self._tmp.name) / "замеры.txt"
        import_pressure.write_template(rows, target)

        text = target.read_text(encoding="utf-8")
        self.assertIn("12.08.2026 09:14", text)
        self.assertIn("# IMG_0.JPG", text)
        self.assertIn("(время файла, не съёмки)", text)

        # незаполненная заготовка не даёт ни записей, ни жалоб
        self.assertEqual(import_pressure.read_rows(target), ([], []))

        filled = text.replace(
            "12.08.2026 09:14", "12.08.2026 09:14 140/90 72", 1
        )
        target.write_text(filled, encoding="utf-8")
        parsed, failed = import_pressure.read_rows(target)
        self.assertEqual(failed, [])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].systolic, 140)


class StorePressureTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "diary.db"
        db = Database(str(self.path), "Europe/Moscow")
        await db.connect()
        await db.ensure_user(777)
        await db.close()

    async def asyncTearDown(self):
        self._tmp.cleanup()

    def rows(self):
        source = Path(self._tmp.name) / "замеры.txt"
        source.write_text(
            "12.08.2026 09:14 140/90 72   # IMG_1.JPG\n"
            "12.08.2026 21:03 128/82 68   # IMG_2.JPG\n",
            encoding="utf-8",
        )
        return import_pressure.read_rows(source)[0]

    async def test_writes_measurements(self):
        added, skipped = await import_pressure.store(self.rows(), self.path, None)
        self.assertEqual((added, skipped), (2, 0))

        db = Database(str(self.path), "Europe/Moscow")
        await db.connect()
        stored = await db.last_measurements(777)
        self.assertEqual([item.bp for item in stored], ["128/82", "140/90"])
        await db.close()

    async def test_second_run_does_not_duplicate(self):
        await import_pressure.store(self.rows(), self.path, None)
        added, skipped = await import_pressure.store(self.rows(), self.path, None)
        self.assertEqual((added, skipped), (0, 2))


if __name__ == "__main__":
    unittest.main()
