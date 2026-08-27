"""Фильтр сетевых обрывов: стена красного текста должна стать одной строкой."""

from __future__ import annotations

import logging
import unittest

from bot.netlog import FlakyNetworkFilter

FAILED = (
    "Failed to fetch updates - TelegramNetworkError: HTTP Client says - "
    "ServerDisconnectedError: Server disconnected"
)
SLEEP = "Sleep for 12.000000 seconds and try again... (tryings = 1, bot id = 42)"
ESTABLISHED = "Connection established to Telegram API"


def record(message: str, level: int = logging.ERROR) -> logging.LogRecord:
    return logging.LogRecord("aiogram.dispatcher", level, __file__, 1, message, (), None)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.filter = FlakyNetworkFilter(quiet_seconds=300, clock=self.clock)

    def test_first_hiccup_becomes_a_calm_warning(self):
        item = record(FAILED)
        self.assertTrue(self.filter.filter(item))
        self.assertEqual(item.levelname, "WARNING")
        self.assertIn("VPN", item.getMessage())

    def test_next_ones_are_swallowed(self):
        self.filter.filter(record(FAILED))
        for message in (SLEEP, ESTABLISHED, FAILED, SLEEP):
            self.assertFalse(self.filter.filter(record(message)))

    def test_one_summary_per_quiet_period(self):
        self.filter.filter(record(FAILED))
        for _ in range(9):
            self.filter.filter(record(FAILED))

        self.clock.value = 301
        item = record(FAILED)
        self.assertTrue(self.filter.filter(item))
        self.assertIn("10 раз", item.getMessage())
        self.assertIn("Бот работает", item.getMessage())

    def test_real_errors_pass_through_untouched(self):
        for message in (
            "Cause exception while process update id=1 by bot id=42",
            "Failed to fetch updates - TelegramUnauthorizedError: Unauthorized",
            "Ошибка в обработчике /stats",
        ):
            with self.subTest(message=message):
                item = record(message)
                self.assertTrue(self.filter.filter(item))
                self.assertEqual(item.levelname, "ERROR")
                self.assertEqual(item.getMessage(), message)

    def test_traceback_is_dropped_from_the_rewritten_line(self):
        item = record(FAILED)
        item.exc_info = (ValueError, ValueError("boom"), None)
        self.filter.filter(item)
        self.assertIsNone(item.exc_info)


class PollingTimeoutTest(unittest.TestCase):
    """Длина long poll: короткий запрос успевает вернуться до обрыва."""

    def load(self, value=None):
        import os
        from unittest import mock

        from bot.config import load_config

        env = {"BOT_TOKEN": "42:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"}
        if value is not None:
            env["POLLING_TIMEOUT"] = value
        # .env владельца в тест не подмешиваем: проверяем сам разбор настроек
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "bot.config.load_dotenv", lambda *a, **kw: None
        ):
            return load_config().polling_timeout

    def test_default_is_short(self):
        self.assertEqual(self.load(), 15)

    def test_custom_value(self):
        self.assertEqual(self.load("25"), 25)

    def test_nonsense_and_extremes_are_tamed(self):
        self.assertEqual(self.load("много"), 15)
        self.assertEqual(self.load("0"), 1)
        self.assertEqual(self.load("900"), 50)


if __name__ == "__main__":
    unittest.main()
