"""Агрегаты дневника и текст сводки."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from bot.db import Database, UserSettings
from bot.pressure.db import Measurement
from bot.pressure.stats import (
    build_report,
    day_part,
    period_range,
    render_summary,
    summarize,
    trend_line,
)

NOW = dt.datetime(2026, 8, 17, 14, 30)
USER = UserSettings(user_id=777, tz="Europe/Moscow", target_sys=135, target_dia=85)


def make(systolic, diastolic, pulse=None, moment=NOW, note="") -> Measurement:
    return Measurement(
        id=0,
        systolic=systolic,
        diastolic=diastolic,
        pulse=pulse,
        measured_at=moment,
        note=note,
    )


class PeriodTest(unittest.TestCase):
    def test_week_covers_seven_days(self):
        start, end = period_range("week", NOW)
        self.assertEqual(start, dt.datetime(2026, 8, 11, 0, 0))
        self.assertEqual(end.date(), NOW.date())
        self.assertEqual((end.date() - start.date()).days + 1, 7)

    def test_month_and_quarter(self):
        self.assertEqual(period_range("month", NOW)[0].date(), dt.date(2026, 7, 19))
        self.assertEqual(period_range("quarter", NOW)[0].date(), dt.date(2026, 5, 20))

    def test_all_time_starts_at_epoch(self):
        self.assertEqual(period_range("all", NOW)[0].year, 1970)

    def test_end_of_day_included(self):
        _, end = period_range("week", NOW)
        self.assertEqual((end.hour, end.minute), (23, 59))


class DayPartTest(unittest.TestCase):
    def test_buckets(self):
        cases = {6: "morning", 11: "morning", 12: "afternoon", 17: "afternoon",
                 18: "evening", 22: "evening", 23: "night", 2: "night"}
        for hour, key in cases.items():
            with self.subTest(hour=hour):
                self.assertEqual(day_part(NOW.replace(hour=hour))[0], key)


class SummarizeTest(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(summarize([], 135, 85))

    def test_averages_and_spread(self):
        summary = summarize(
            [make(120, 80, 60), make(130, 90, 70), make(140, 100, 80)], 135, 85
        )
        self.assertEqual((summary.avg_sys, summary.avg_dia, summary.avg_pulse), (130, 90, 70))
        self.assertEqual((summary.min_sys, summary.max_sys), (120, 140))
        self.assertEqual((summary.min_dia, summary.max_dia), (80, 100))
        self.assertEqual(summary.count, 3)

    def test_pulse_optional(self):
        summary = summarize([make(120, 80), make(130, 84)], 135, 85)
        self.assertIsNone(summary.avg_pulse)

    def test_target_share(self):
        summary = summarize(
            [make(120, 80), make(130, 84), make(150, 95), make(134, 84)], 135, 85
        )
        self.assertEqual(summary.in_target, 3)
        self.assertEqual(summary.target_share, 75)

    def test_grades_sorted_worst_first(self):
        summary = summarize([make(115, 75), make(165, 100), make(135, 86)], 135, 85)
        self.assertEqual([grade.key for grade, _ in summary.grades],
                         ["ag2", "high_normal", "optimal"])

    def test_day_parts(self):
        summary = summarize(
            [
                make(140, 90, moment=NOW.replace(hour=8)),
                make(130, 85, moment=NOW.replace(hour=9)),
                make(120, 80, moment=NOW.replace(hour=20)),
            ],
            135,
            85,
        )
        parts = {part.key: part for part in summary.parts}
        self.assertEqual(parts["morning"].count, 2)
        self.assertEqual((parts["morning"].avg_sys, parts["morning"].avg_dia), (135, 88))
        self.assertEqual(parts["evening"].count, 1)

    def test_daily_series(self):
        summary = summarize(
            [
                make(120, 80, moment=dt.datetime(2026, 8, 15, 9, 0)),
                make(140, 90, moment=dt.datetime(2026, 8, 15, 21, 0)),
                make(130, 85, moment=dt.datetime(2026, 8, 16, 9, 0)),
            ],
            135,
            85,
        )
        self.assertEqual(summary.days_covered, 2)
        self.assertEqual([day.avg_sys for day in summary.days], [130, 130])
        self.assertEqual(summary.days[0].max_sys, 140)

    def test_crisis_counted(self):
        summary = summarize([make(185, 95), make(120, 80), make(150, 125)], 135, 85)
        self.assertEqual(summary.crisis, 2)

    def test_peaks_are_highest_first(self):
        summary = summarize([make(120, 80), make(180, 95), make(150, 90)], 135, 85)
        self.assertEqual([m.systolic for m in summary.peaks], [180, 150, 120])


class TrendTest(unittest.TestCase):
    def make_summary(self, systolic, diastolic, count=5):
        return summarize([make(systolic, diastolic)] * count, 135, 85)

    def test_needs_enough_data(self):
        self.assertIsNone(trend_line(self.make_summary(120, 80), None))
        self.assertIsNone(
            trend_line(self.make_summary(120, 80), self.make_summary(130, 85, count=2))
        )

    def test_direction(self):
        lower = trend_line(self.make_summary(120, 80), self.make_summary(140, 90))
        self.assertIn("ниже", lower)
        self.assertIn("20/10", lower)

        higher = trend_line(self.make_summary(140, 90), self.make_summary(120, 80))
        self.assertIn("выше", higher)

    def test_flat(self):
        self.assertIn("ровно", trend_line(self.make_summary(120, 80), self.make_summary(121, 81)))


class RenderTest(unittest.TestCase):
    def test_summary_block_has_key_numbers(self):
        summary = summarize([make(120, 80, 65), make(150, 95, 75)], 135, 85)
        text = "\n".join(render_summary(summary, USER, trend=None))
        self.assertIn("135/88", text)  # среднее
        self.assertIn("В целевом", text)
        self.assertIn("Распределение", text)

    def test_crisis_is_called_out(self):
        summary = summarize([make(190, 100)], 135, 85)
        text = "\n".join(render_summary(summary, USER, trend=None))
        self.assertIn("Кризовых", text)


class BuildReportTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "test.db"), "Europe/Moscow")
        await self.db.connect()
        await self.db.ensure_user(USER.user_id)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def test_empty_diary(self):
        text = await build_report(self.db, USER, "month", NOW)
        self.assertIn("пуст", text)

    async def test_empty_period_but_not_empty_diary(self):
        await self.db.add_measurement(
            USER.user_id, 120, 80, 65, NOW - dt.timedelta(days=200)
        )
        text = await build_report(self.db, USER, "week", NOW)
        self.assertIn("записей нет", text)

    async def test_report_has_numbers(self):
        for day in range(10):
            await self.db.add_measurement(
                USER.user_id, 130 + day, 85, 70, NOW - dt.timedelta(days=day)
            )
        text = await build_report(self.db, USER, "month", NOW)
        self.assertIn("Среднее", text)
        self.assertIn("Динамика", text)
        self.assertIn("10 измерений", text)

    async def test_trend_compares_with_previous_period(self):
        for day in range(7):
            await self.db.add_measurement(
                USER.user_id, 120, 80, 65, NOW - dt.timedelta(days=day)
            )
        for day in range(8, 14):
            await self.db.add_measurement(
                USER.user_id, 150, 95, 80, NOW - dt.timedelta(days=day)
            )
        text = await build_report(self.db, USER, "week", NOW)
        self.assertIn("Периодом раньше", text)
        self.assertIn("ниже", text)


if __name__ == "__main__":
    unittest.main()
