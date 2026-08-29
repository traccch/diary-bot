"""Диктовка: числа словами становятся цифрами, остальное не трогаем."""

from __future__ import annotations

import datetime as dt
import unittest

from bot.speech import words_to_numbers
from bot.voice import clean_speech

NOW = dt.datetime(2026, 8, 29, 7, 40)


class WordsToNumbersTest(unittest.TestCase):
    def check(self, said: str, expected: str) -> None:
        self.assertEqual(words_to_numbers(said), expected)

    def test_pressure_as_it_is_dictated(self):
        self.check("сто двадцать на восемьдесят", "120 на 80")
        self.check("сто тридцать пять на девяносто", "135 на 90")

    def test_pulse_and_the_rest_survive(self):
        self.check(
            "сто двадцать на восемьдесят пульс шестьдесят восемь",
            "120 на 80 пульс 68",
        )

    def test_thousands(self):
        self.check("восемь тысяч двести шагов", "8200 шагов")
        self.check("тысяча рублей", "1000 рублей")
        self.check("двести три тысячи сто шестнадцать", "203116")

    def test_two_numbers_in_a_row_stay_two(self):
        """«Двадцать сто» — это два числа: разряды не убывают."""
        self.check("двадцать сто", "20 100")

    def test_digits_are_left_alone(self):
        self.check("120 на 80", "120 на 80")
        self.check("кофе 300", "кофе 300")

    def test_words_that_are_not_numbers(self):
        self.check("продукты и хозтовары", "продукты и хозтовары")
        self.check("", "")

    def test_case_and_yo(self):
        self.check("Сто Двадцать", "120")
        self.check("шестьдесят восемь", "68")

    def test_punctuation_stays(self):
        self.check("сто двадцать на восемьдесят, пульс шестьдесят восемь.",
                   "120 на 80, пульс 68.")


class DictationTest(unittest.TestCase):
    """Сказанное вслух должно доходить до дневника целиком."""

    def entry(self, said: str):
        from bot.pressure.parsing import parse_entry

        return parse_entry(clean_speech(said), NOW)

    def test_measurement_by_voice(self):
        measurement = self.entry("Сто двадцать на восемьдесят, пульс шестьдесят восемь.").measurement
        self.assertEqual(
            (measurement.systolic, measurement.diastolic, measurement.pulse),
            (120, 80, 68),
        )

    def test_metric_by_voice(self):
        metrics = self.entry("Шаги восемь тысяч двести").metrics
        self.assertEqual([(item.kind, item.value) for item in metrics], [("steps", 8200)])

    def test_expense_by_voice(self):
        from bot.money.parsing import parse_transaction

        parsed = parse_transaction(clean_speech("Кофе триста рублей"), NOW.date())
        self.assertEqual(parsed.amount, 30000)
        self.assertEqual(parsed.note.lower(), "кофе")

    def test_digits_from_whisper_also_work(self):
        """Whisper пишет как слышит: иногда цифрами — тогда менять нечего."""
        measurement = self.entry("120/80 68").measurement
        self.assertEqual(measurement.systolic, 120)


if __name__ == "__main__":
    unittest.main()
