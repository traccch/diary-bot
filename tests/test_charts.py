"""Графики и PDF. Пропускаются, если matplotlib не установлен."""

from __future__ import annotations

import datetime as dt
import re
import unittest

from bot import charts
from bot.db import Measurement, UserSettings
from bot.export import csv_bytes, table_rows, text_report
from bot.stats import summarize

USER = UserSettings(user_id=777, tz="Europe/Moscow", target_sys=135, target_dia=85)
NOW = dt.datetime(2026, 8, 17, 14, 30)


def series(count: int, *, with_pulse: bool = True, per_day: int = 1) -> list[Measurement]:
    measurements = []
    for index in range(count):
        day, slot = divmod(index, per_day)
        moment = NOW - dt.timedelta(days=count // per_day - day, hours=slot * 6)
        measurements.append(
            Measurement(
                id=index + 1,
                systolic=120 + (index % 25),
                diastolic=78 + (index % 12),
                pulse=64 + (index % 15) if with_pulse else None,
                measured_at=moment,
                note="после прогулки" if index % 5 == 0 else "",
            )
        )
    return sorted(measurements, key=lambda m: m.measured_at)


class ExportTest(unittest.TestCase):
    def test_csv_opens_in_excel(self):
        payload = csv_bytes(series(3))
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))  # BOM
        text = payload.decode("utf-8-sig")
        self.assertIn("верхнее;нижнее", text.replace(";пульс", ""))
        self.assertEqual(len(text.strip().splitlines()), 4)

    def test_table_columns_never_touch(self):
        """Самое длинное название категории не должно слипаться с комментарием."""
        longest = Measurement(
            id=1,
            systolic=150,  # изолированная систолическая АГ — 30 символов в названии
            diastolic=85,
            pulse=72,
            measured_at=NOW,
            note="после кофе",
        )
        row = table_rows([longest])[-1]
        self.assertIn("Изолированная систолическая АГ", row)
        self.assertIn("Изолированная систолическая АГ  после кофе", row)
        self.assertNotIn("АГпосле", row)

    def test_table_truncates_long_notes_with_ellipsis(self):
        long_note = Measurement(
            id=1, systolic=120, diastolic=80, pulse=70, measured_at=NOW,
            note="очень длинный комментарий про самочувствие и лекарства",
        )
        row = table_rows([long_note])[-1]
        self.assertTrue(row.endswith("…"), row)
        self.assertLessEqual(len(row), len(table_rows([long_note])[0]) + 20)

    def test_text_report_has_summary_and_table(self):
        measurements = series(10)
        summary = summarize(measurements, USER.target_sys, USER.target_dia)
        text = text_report(measurements, summary, USER, NOW).decode("utf-8-sig")
        self.assertIn("ДНЕВНИК АРТЕРИАЛЬНОГО ДАВЛЕНИЯ", text)
        self.assertIn("Распределение по категориям", text)
        self.assertIn("135/85", text)  # порог домашних измерений в примечании


@unittest.skipUnless(charts.available(), "matplotlib не установлен")
class ChartsTest(unittest.TestCase):
    def test_png_is_produced(self):
        image = charts.pressure_png(series(20), USER, "Давление за 30 дней")
        self.assertTrue(image.startswith(b"\x89PNG"))
        self.assertGreater(len(image), 10_000)

    def test_png_without_pulse(self):
        image = charts.pressure_png(series(12, with_pulse=False), USER, "Давление")
        self.assertTrue(image.startswith(b"\x89PNG"))

    def test_long_series_is_aggregated_by_day(self):
        image = charts.pressure_png(series(200, per_day=3), USER, "Давление за всё время")
        self.assertTrue(image.startswith(b"\x89PNG"))

    def test_single_point_is_rejected(self):
        with self.assertRaises(ValueError):
            charts.pressure_png(series(1), USER, "Давление")

    def test_pdf_has_summary_and_pages(self):
        measurements = series(90, per_day=2)
        summary = summarize(measurements, USER.target_sys, USER.target_dia)
        payload = charts.doctor_pdf(measurements, summary, USER, NOW)
        self.assertTrue(payload.startswith(b"%PDF"))
        # титульная страница плюс страницы таблицы
        pages = int(re.search(rb"/Count (\d+)", payload).group(1))
        self.assertGreaterEqual(pages, 3)

    def test_pdf_survives_a_short_diary(self):
        measurements = series(2)
        summary = summarize(measurements, USER.target_sys, USER.target_dia)
        self.assertTrue(
            charts.doctor_pdf(measurements, summary, USER, NOW).startswith(b"%PDF")
        )


if __name__ == "__main__":
    unittest.main()
