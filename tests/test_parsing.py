"""Разбор свободного текста в измерение."""

from __future__ import annotations

import datetime as dt
import unittest

from bot.pressure.parsing import ParseError, parse_measurement, parse_time

NOW = dt.datetime(2026, 8, 17, 14, 30)


class ParseMeasurementTest(unittest.TestCase):
    def parse(self, text: str, now: dt.datetime = NOW):
        return parse_measurement(text, now)

    def test_plain_pressure(self):
        parsed = self.parse("120/80")
        self.assertEqual((parsed.systolic, parsed.diastolic), (120, 80))
        self.assertIsNone(parsed.pulse)
        self.assertEqual(parsed.measured_at, NOW)
        self.assertEqual(parsed.note, "")

    def test_separators(self):
        for text in ("120/80", "120\\80", "120-80", "120 80", "120 на 80", "120|80"):
            with self.subTest(text=text):
                parsed = self.parse(text)
                self.assertEqual((parsed.systolic, parsed.diastolic), (120, 80))

    def test_pulse_forms(self):
        for text in ("120/80 68", "120/80/68", "120 80 68", "120/80 п68", "120/80 пульс 68"):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text).pulse, 68)

    def test_note_is_everything_else(self):
        parsed = self.parse("135/85 72 после прогулки")
        self.assertEqual(parsed.pulse, 72)
        self.assertEqual(parsed.note, "после прогулки")

    def test_note_keeps_its_own_numbers(self):
        parsed = self.parse("120/80 после 2 таблеток")
        self.assertIsNone(parsed.pulse)
        self.assertEqual(parsed.note, "после 2 таблеток")

    def test_noise_words_dropped_from_note(self):
        self.assertEqual(self.parse("давление 120/80").note, "")
        self.assertEqual(self.parse("измерил 120/80 голова болит").note, "голова болит")

    def test_relative_day(self):
        parsed = self.parse("130/85 вчера")
        self.assertEqual(parsed.measured_at.date(), dt.date(2026, 8, 16))
        self.assertEqual(parsed.measured_at.time(), dt.time(14, 30))
        self.assertEqual(parsed.note, "")

    def test_explicit_time(self):
        parsed = self.parse("130/85 21:30")
        self.assertEqual(parsed.measured_at, dt.datetime(2026, 8, 17, 21, 30))

    def test_time_with_dot(self):
        parsed = self.parse("118/76 8.30")
        self.assertEqual(parsed.measured_at, dt.datetime(2026, 8, 17, 8, 30))

    def test_date_and_time(self):
        parsed = self.parse("140/90 15.08 09:00 после кофе")
        self.assertEqual(parsed.measured_at, dt.datetime(2026, 8, 15, 9, 0))
        self.assertEqual(parsed.note, "после кофе")

    def test_date_from_last_year_when_in_future(self):
        parsed = self.parse("120/80 25.12")
        self.assertEqual(parsed.measured_at.date(), dt.date(2025, 12, 25))

    def test_word_order_does_not_matter(self):
        parsed = self.parse("вчера утром 128/84 66 перед завтраком")
        self.assertEqual((parsed.systolic, parsed.diastolic, parsed.pulse), (128, 84, 66))
        self.assertEqual(parsed.measured_at.date(), dt.date(2026, 8, 16))
        self.assertIn("завтраком", parsed.note)

    def test_no_pressure_returns_none(self):
        for text in ("привет", "", "как дела", "120"):
            with self.subTest(text=text):
                self.assertIsNone(self.parse(text))

    def test_swapped_values_rejected(self):
        with self.assertRaises(ParseError):
            self.parse("80/120")

    def test_out_of_range_rejected(self):
        with self.assertRaises(ParseError):
            self.parse("400/200")

    def test_absurd_pulse_rejected(self):
        with self.assertRaises(ParseError):
            self.parse("120/80 п300")

    def test_seconds_are_trimmed(self):
        parsed = self.parse("120/80", dt.datetime(2026, 8, 17, 14, 30, 59, 123))
        self.assertEqual(parsed.measured_at, dt.datetime(2026, 8, 17, 14, 30))


class ParseTimeTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_time("8:00"), dt.time(8, 0))
        self.assertEqual(parse_time("08.00"), dt.time(8, 0))
        self.assertEqual(parse_time("0800"), dt.time(8, 0))
        self.assertEqual(parse_time("8"), dt.time(8, 0))
        self.assertEqual(parse_time("21:45"), dt.time(21, 45))

    def test_garbage(self):
        for text in ("утром", "", "25:00", "8:99", "abc"):
            with self.subTest(text=text):
                self.assertIsNone(parse_time(text))


if __name__ == "__main__":
    unittest.main()
