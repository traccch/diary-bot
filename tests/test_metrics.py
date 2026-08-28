"""Показатели здоровья: разбор, хранение, связь с давлением."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest

from bot.pressure import metrics
from bot.db import UserSettings
from bot.pressure.db import Measurement, Metric
from bot.pressure.parsing import ParseError, parse_entry
from bot.pressure.stats import (
    build_health_block,
    collect_health,
    correlate,
    health_lines_plain,
    render_correlation,
    summarize_metric,
)

from .support import memory_db

NOW = dt.datetime(2026, 8, 17, 8, 30)
TODAY = NOW.date()
USER = UserSettings(user_id=777, tz="Europe/Moscow", target_sys=135, target_dia=85)


class ParseSleepTest(unittest.TestCase):
    def metrics_of(self, text: str) -> dict[str, float]:
        return {item.kind: item.value for item in parse_entry(text, NOW).metrics}

    def test_range_over_midnight(self):
        entry = parse_entry("сон 23:21-7:01", NOW)
        sleep = entry.metrics[0]
        self.assertEqual(sleep.kind, "sleep")
        self.assertEqual(sleep.value, 460)  # 7 ч 40 мин
        self.assertEqual(sleep.extra, "23:21-07:01")

    def test_range_after_midnight(self):
        self.assertEqual(self.metrics_of("сон 0:30-7:00")["sleep"], 390)

    def test_range_wording(self):
        for text in ("спал с 23:40 до 6:50", "сон 23:40-6:50", "спала 23:40 - 6:50"):
            with self.subTest(text=text):
                self.assertEqual(self.metrics_of(text)["sleep"], 430)

    def test_duration_forms(self):
        cases = {
            "сон 7ч40м": 460,
            "сон 7 часов": 420,
            "спал 8 час": 480,
            "сон 6 ч 45 мин": 405,
            "сон 7:40": 460,
        }
        for text, minutes in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.metrics_of(text)["sleep"], minutes)

    def test_absurd_sleep_rejected(self):
        with self.assertRaises(ParseError):
            parse_entry("сон 30 часов", NOW)


class ParseOtherMetricsTest(unittest.TestCase):
    def metrics_of(self, text: str) -> dict[str, float]:
        return {item.kind: item.value for item in parse_entry(text, NOW).metrics}

    def test_steps(self):
        for text in ("шаги 8200", "8200 шагов", "шагов 8200"):
            with self.subTest(text=text):
                self.assertEqual(self.metrics_of(text)["steps"], 8200)

    def test_resting_pulse_forms(self):
        for text in ("пульс покоя 58", "пульс в покое 58", "пп 58", "rhr 58"):
            with self.subTest(text=text):
                self.assertEqual(self.metrics_of(text)["resting_pulse"], 58)

    def test_resting_pulse_does_not_eat_measurement_pulse(self):
        entry = parse_entry("120/80 68 пульс покоя 58", NOW)
        self.assertEqual(entry.measurement.pulse, 68)
        self.assertEqual(entry.metrics[0].value, 58)

    def test_weight(self):
        for text, value in (("вес 78.5", 78.5), ("вес 78,5", 78.5), ("78,5 кг", 78.5)):
            with self.subTest(text=text):
                self.assertEqual(self.metrics_of(text)["weight"], value)

    def test_everything_at_once(self):
        entry = parse_entry("120/80 68 сон 23:00-6:30 шаги 9300 вес 78,4 норм день", NOW)
        self.assertEqual(entry.measurement.systolic, 120)
        self.assertEqual(entry.measurement.pulse, 68)
        self.assertEqual(
            {item.kind: item.value for item in entry.metrics},
            {"sleep": 450, "steps": 9300, "weight": 78.4},
        )
        self.assertEqual(entry.note, "норм день")

    def test_metrics_without_pressure(self):
        entry = parse_entry("шаги 8200 пп 56", NOW)
        self.assertIsNone(entry.measurement)
        self.assertEqual(len(entry.metrics), 2)
        self.assertTrue(entry)

    def test_nothing_at_all(self):
        self.assertFalse(parse_entry("привет", NOW))

    def test_metric_date_follows_the_measurement(self):
        entry = parse_entry("120/80 вчера сон 23:00-7:00", NOW)
        self.assertEqual(entry.on_date, TODAY - dt.timedelta(days=1))

    def test_out_of_range_rejected(self):
        for text in ("шаги 500000", "вес 500", "пульс покоя 300"):
            with self.subTest(text=text):
                with self.assertRaises(ParseError):
                    parse_entry(text, NOW)


class FormatTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(metrics.format_value("sleep", 460), "7 ч 40 мин")
        self.assertEqual(metrics.format_value("sleep", 420), "7 ч")
        self.assertEqual(metrics.format_value("steps", 9150), "9 150 шагов")
        self.assertEqual(metrics.format_value("steps", 9150, short=True), "9 150")
        self.assertEqual(metrics.format_value("weight", 78.5), "78,5 кг")
        self.assertEqual(metrics.format_value("weight", 79.0), "79 кг")
        self.assertEqual(metrics.format_value("resting_pulse", 58), "58 уд/мин")

    def test_sleep_is_drawn_in_hours(self):
        self.assertEqual(metrics.chart_value("sleep", 450), 7.5)
        self.assertEqual(metrics.chart_value("steps", 8200), 8200)

    def test_median(self):
        self.assertEqual(metrics.median([1, 2, 3]), 2)
        self.assertEqual(metrics.median([1, 2, 3, 4]), 2.5)


def measurement(systolic, diastolic, moment) -> Measurement:
    return Measurement(
        id=0, systolic=systolic, diastolic=diastolic, pulse=70, measured_at=moment
    )


class CorrelationTest(unittest.TestCase):
    def build(self, pattern: list[tuple[int, int, int]]):
        """pattern: (сон в минутах, верхнее, нижнее) по дням."""
        values, measurements = [], []
        for index, (sleep, systolic, diastolic) in enumerate(pattern):
            date = dt.date(2026, 7, 1) + dt.timedelta(days=index)
            values.append(Metric("sleep", date, sleep))
            measurements.append(
                measurement(systolic, diastolic, dt.datetime.combine(date, dt.time(8, 0)))
            )
        return measurements, values

    def test_needs_enough_days(self):
        measurements, values = self.build([(400, 130, 85)] * 6)
        self.assertIsNone(correlate(measurements, values, metrics.SLEEP, True))

    def test_finds_the_link(self):
        pattern = [(330, 145, 92)] * 8 + [(480, 130, 82)] * 8
        measurements, values = self.build(pattern)
        found = correlate(measurements, values, metrics.SLEEP, morning_only=True)
        self.assertIsNotNone(found)
        self.assertEqual((found.low_sys, found.low_dia), (145, 92))
        self.assertEqual((found.high_sys, found.high_dia), (130, 82))
        self.assertEqual(found.delta_sys, 15)
        self.assertTrue(found.meaningful)

        text = "\n".join(render_correlation(found))
        self.assertIn("После коротких ночей давление выше на 15/10", text)

    def test_no_link_is_stated_plainly(self):
        pattern = [(330, 130, 84)] * 8 + [(480, 131, 85)] * 8
        measurements, values = self.build(pattern)
        found = correlate(measurements, values, metrics.SLEEP, morning_only=True)
        self.assertFalse(found.meaningful)
        self.assertIn("в пределах погрешности", "\n".join(render_correlation(found)))

    def test_evening_measurements_ignored_for_sleep(self):
        pattern = [(330, 145, 92), (480, 130, 82)] * 6
        measurements, values = self.build(pattern)
        evening = [
            measurement(200, 120, m.measured_at.replace(hour=21)) for m in measurements
        ]
        found = correlate(measurements + evening, values, metrics.SLEEP, morning_only=True)
        self.assertEqual(found.low_count + found.high_count, 12)
        self.assertEqual(found.low_sys, 145)  # вечерние 200/120 не попали в расчёт

    def test_identical_values_give_nothing(self):
        measurements, values = self.build([(400, 130, 85)] * 14)
        self.assertIsNone(correlate(measurements, values, metrics.SLEEP, True))


class MetricStorageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = memory_db()
        await self.db.connect()
        await self.db.ensure_user(USER.user_id)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def test_one_value_per_day_replaces(self):
        await self.db.set_metric(USER.user_id, "steps", TODAY, 5000)
        await self.db.set_metric(USER.user_id, "steps", TODAY, 8200)
        stored = await self.db.metrics_between(USER.user_id, "steps", TODAY, TODAY)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].value, 8200)

    async def test_sleep_keeps_the_schedule(self):
        await self.db.set_metric(USER.user_id, "sleep", TODAY, 460, "23:21-07:01")
        stored = await self.db.get_metric(USER.user_id, "sleep", TODAY)
        self.assertEqual(stored.extra, "23:21-07:01")

    async def test_isolated_between_users(self):
        await self.db.set_metric(USER.user_id, "weight", TODAY, 78.5)
        await self.db.ensure_user(USER.user_id + 1)
        self.assertIsNone(await self.db.get_metric(USER.user_id + 1, "weight", TODAY))

    async def test_period_and_last(self):
        for offset in range(5):
            date = TODAY - dt.timedelta(days=offset)
            await self.db.set_metric(USER.user_id, "weight", date, 80 - offset)
        window = await self.db.metrics_between(
            USER.user_id, "weight", TODAY - dt.timedelta(days=2), TODAY
        )
        self.assertEqual([item.value for item in window], [78, 79, 80])
        self.assertEqual((await self.db.last_metric(USER.user_id, "weight")).value, 80)

    async def test_delete_and_count(self):
        await self.db.set_metric(USER.user_id, "steps", TODAY, 100)
        self.assertEqual(await self.db.count_metrics(USER.user_id), 1)
        self.assertTrue(await self.db.delete_metric(USER.user_id, "steps", TODAY))
        self.assertFalse(await self.db.delete_metric(USER.user_id, "steps", TODAY))

    async def test_summary_of_a_metric(self):
        for offset, value in enumerate([79.0, 78.6, 78.2]):
            await self.db.set_metric(
                USER.user_id, "weight", TODAY - dt.timedelta(days=2 - offset), value
            )
        stored = await self.db.metrics_between(
            USER.user_id, "weight", TODAY - dt.timedelta(days=7), TODAY
        )
        summary = summarize_metric(metrics.WEIGHT, stored)
        self.assertEqual(summary.count, 3)
        self.assertAlmostEqual(summary.first, 79.0)
        self.assertAlmostEqual(summary.latest, 78.2)
        self.assertAlmostEqual(summary.delta, -0.8)

    async def test_health_block_and_plain_lines(self):
        start = dt.datetime.combine(TODAY - dt.timedelta(days=29), dt.time.min)
        end = dt.datetime.combine(TODAY, dt.time(23, 59))
        for offset in range(16):
            date = TODAY - dt.timedelta(days=offset)
            short = offset % 2 == 0
            await self.db.set_metric(USER.user_id, "sleep", date, 330 if short else 480)
            await self.db.add_measurement(
                USER.user_id,
                146 if short else 130,
                92 if short else 82,
                70,
                dt.datetime.combine(date, dt.time(8, 0)),
            )
        measurements = await self.db.measurements_between(USER.user_id, start, end)

        block = "\n".join(
            await build_health_block(self.db, USER, measurements, start, end)
        )
        self.assertIn("Здоровье", block)
        self.assertIn("Сон", block)
        self.assertIn("После коротких ночей давление выше", block)

        plain = "\n".join(
            health_lines_plain(
                *await collect_health(self.db, USER, measurements, start, end)
            )
        )
        self.assertIn("Показатели здоровья", plain)
        self.assertNotIn("<b>", plain)
        self.assertIn("медиана", plain)

    async def test_health_block_is_empty_without_data(self):
        start = dt.datetime.combine(TODAY, dt.time.min)
        end = dt.datetime.combine(TODAY, dt.time(23, 59))
        self.assertEqual(await build_health_block(self.db, USER, [], start, end), [])


if __name__ == "__main__":
    unittest.main()
