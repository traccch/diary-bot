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
        self.assertIn("/proxy auto", item.getMessage())

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

    def test_hint_depends_on_the_proxy(self):
        """Советовать прокси тому, у кого он включён, — значит его не слушать."""
        item = record(FAILED)
        self.filter.filter(item)
        self.assertIn("/proxy auto", item.getMessage())

        self.filter._last_report = None  # как при новом запуске
        self.filter._cause = None
        self.filter.proxied = True

        item = record(FAILED)
        self.filter.filter(item)
        self.assertIn("хотя прокси включён", item.getMessage())
        self.assertNotIn("/proxy auto", item.getMessage())

    def test_server_errors_get_their_own_explanation(self):
        """502 — это Telegram приболел, и советовать тут VPN просто вредно."""
        item = record(
            "Failed to fetch updates - TelegramServerError: Telegram server says"
            " - Bad Gateway"
        )
        self.assertTrue(self.filter.filter(item))
        text = item.getMessage()
        self.assertIn("на его стороне", text)
        self.assertNotIn("прокси тут при", text)
        self.assertEqual(item.levelname, "WARNING")

    def test_change_of_cause_is_reported_at_once(self):
        self.filter.filter(record(FAILED))
        self.filter.filter(record(FAILED))

        server = record("Failed to fetch updates - TelegramServerError: Bad Gateway")
        self.assertTrue(self.filter.filter(server))
        self.assertIn("на его стороне", server.getMessage())

    def test_repeats_name_the_right_cause(self):
        server = "Failed to fetch updates - TelegramServerError: Bad Gateway"
        self.filter.filter(record(server))
        for _ in range(4):
            self.filter.filter(record(server))

        self.clock.value = 301
        item = record(server)
        self.assertTrue(self.filter.filter(item))
        self.assertIn("Telegram отвечал ошибкой ещё 5 раз", item.getMessage())

    def test_endless_drops_are_reported_ever_less_often(self):
        """Череда одинаковых предупреждений сама становится шумом."""
        self.filter.filter(record(FAILED))  # первое — сразу

        said = []
        for minute in range(1, 120):
            self.clock.value = minute * 60
            if self.filter.filter(record(FAILED)):
                said.append(minute)

        # 5 минут, потом 15, потом 45 — а не каждые пять
        self.assertEqual(said[:3], [5, 20, 65])

    def test_backoff_resets_when_the_cause_changes(self):
        self.filter.filter(record(FAILED))
        self.clock.value = 300
        self.filter.filter(record(FAILED))

        server = record("Failed to fetch updates - TelegramServerError: Bad Gateway")
        self.clock.value = 400
        self.assertTrue(self.filter.filter(server))

        self.clock.value = 700  # снова пять минут, отсчёт начался заново
        self.assertTrue(self.filter.filter(record("Failed to fetch updates - TelegramServerError: Bad Gateway")))

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


class ProxyConfigTest(unittest.TestCase):
    """TELEGRAM_PROXY: адрес локального прокси, а не ключ VPN."""

    def test_address_is_taken_as_is(self):
        from bot.config import read_proxy

        self.assertEqual(read_proxy(" socks5://127.0.0.1:2080 "), "socks5://127.0.0.1:2080")
        self.assertEqual(read_proxy("http://127.0.0.1:8080"), "http://127.0.0.1:8080")
        self.assertEqual(read_proxy(""), "")

    def test_bare_host_and_port_get_a_scheme(self):
        from bot.config import read_proxy

        self.assertEqual(read_proxy("127.0.0.1:2080"), "socks5://127.0.0.1:2080")

    def test_vpn_key_is_refused_with_an_explanation(self):
        from bot.config import read_proxy

        for key in ("vless://uuid@host:443?security=reality", "ss://abcdef@host:8388"):
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError) as caught:
                    read_proxy(key)
                self.assertIn("не адрес прокси", str(caught.exception))
                self.assertIn("socks5://", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
