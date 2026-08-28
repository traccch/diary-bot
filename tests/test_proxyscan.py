"""Поиск локального прокси: бот ищет порт сам, чтобы человек не искал."""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from bot import proxyscan
from bot.config import read_proxy

from .test_handlers import BotTestCase


class ReadProxyTest(unittest.TestCase):
    def test_auto_is_a_request_not_an_address(self):
        self.assertEqual(read_proxy("auto"), "auto")
        self.assertEqual(read_proxy(" АВТО "), "auto")

    def test_address_still_wins(self):
        self.assertEqual(read_proxy("socks5://127.0.0.1:2080"), "socks5://127.0.0.1:2080")


class PortScanTest(unittest.IsolatedAsyncioTestCase):
    async def test_finds_an_open_port_that_works(self):
        opened = []

        async def port_open(port, host="127.0.0.1"):
            opened.append(port)
            return port == 10808

        async def works(url):
            return url.startswith("socks5")

        with mock.patch.object(proxyscan, "port_open", port_open), mock.patch.object(
            proxyscan, "works", works
        ):
            self.assertEqual(await proxyscan.find(), "socks5://127.0.0.1:10808")

        self.assertIn(2080, opened)  # начали с самого частого

    async def test_open_port_that_does_not_proxy_is_skipped(self):
        async def port_open(port, host="127.0.0.1"):
            return True

        async def works(url):
            return url == "http://127.0.0.1:7890"

        with mock.patch.object(proxyscan, "port_open", port_open), mock.patch.object(
            proxyscan, "works", works
        ):
            self.assertEqual(await proxyscan.find(), "http://127.0.0.1:7890")

    async def test_nothing_found(self):
        async def port_open(port, host="127.0.0.1"):
            return False

        with mock.patch.object(proxyscan, "port_open", port_open):
            self.assertIsNone(await proxyscan.find())

    async def test_closed_port_is_detected_quickly(self):
        """Порт, на котором никого нет, не должен задерживать запуск."""
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            self.assertTrue(await proxyscan.port_open(port))
        finally:
            server.close()
            await server.wait_closed()

        self.assertFalse(await proxyscan.port_open(port))


class ProxyCommandTest(BotTestCase):
    """Проверка прокси в чате: человеку не видно консоли, а вопрос есть."""

    def patch(self, opened, working=""):
        async def port_open(port, host="127.0.0.1"):
            return port in opened

        async def works(url):
            return url == working

        return mock.patch.object(proxyscan, "port_open", port_open), mock.patch.object(
            proxyscan, "works", works
        )

    async def test_nothing_open_means_no_vpn_here(self):
        first, second = self.patch(opened=set())
        with first, second:
            await self.send("/proxy")

        answer = self.bot.edits[-1]
        self.assertIn("все известные порты", answer)
        self.assertIn("только на телефоне", answer)

    async def test_working_proxy_is_offered(self):
        first, second = self.patch(opened={2080}, working="socks5://127.0.0.1:2080")
        with first, second:
            await self.send("/proxy")

        answer = self.bot.edits[-1]
        self.assertIn("socks5://127.0.0.1:2080", answer)
        # подсказывать файл, когда есть команда, — значит гонять человека зря
        self.assertIn("/proxy auto", answer)
        self.assertNotIn("TELEGRAM_PROXY", answer)

    async def test_open_but_useless_port(self):
        first, second = self.patch(opened={7890})
        with first, second:
            await self.send("/proxy")

        self.assertIn("7890", self.bot.edits[-1])
        self.assertIn("не прокси", self.bot.edits[-1])

    async def test_says_which_file_it_read(self):
        """«Не работает настройка» обычно значит «правил другой файл»."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("BOT_TOKEN=x\nTELEGRAM_PROXY=auto\n", encoding="utf-8")
            self.dp["env_file"] = str(env)

            first, second = self.patch(opened=set())
            with first, second:
                await self.send("/proxy")

        answer = self.bot.edits[-1]
        self.assertIn(".env", answer)
        self.assertIn("TELEGRAM_PROXY в нём", answer)
        self.assertIn("auto", answer)

    async def test_notepad_leftover_is_pointed_out(self):
        """Блокнот дописывает .txt, проводник расширение прячет."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("BOT_TOKEN=x\n", encoding="utf-8")
            twin = Path(tmp) / ".env.txt"
            twin.write_text("TELEGRAM_PROXY=auto\n", encoding="utf-8")

            self.dp["env_file"] = str(env)
            self.dp["env_lookalikes"] = (str(twin),)

            first, second = self.patch(opened=set())
            with first, second:
                await self.send("/proxy")

        answer = self.bot.edits[-1]
        self.assertIn(".env.txt", answer)
        self.assertIn("переименуй", answer.lower())

    async def test_shows_what_the_bot_uses_now(self):
        self.dp["proxy_now"] = "socks5://127.0.0.1:10808"
        first, second = self.patch(opened=set())
        with first, second:
            await self.send("/proxy")

        self.assertIn("socks5://127.0.0.1:10808", self.bot.edits[-1])


class SetProxyFromChatTest(BotTestCase):
    """Настройка из чата: до файла с телефона не дотянуться."""

    async def test_auto_is_remembered_and_applied(self):
        await self.send("/proxy auto")

        self.assertIn("искать локальный прокси", self.bot.texts[-1])
        self.assertEqual(await self.db.get_meta("proxy"), "auto")
        self.assertTrue(self.restart_event.is_set())  # перезапуск, чтобы применить

    async def test_address_is_accepted(self):
        await self.send("/proxy socks5://127.0.0.1:2080")
        self.assertEqual(await self.db.get_meta("proxy"), "socks5://127.0.0.1:2080")

    async def test_off_returns_to_direct(self):
        await self.db.set_meta("proxy", "auto")
        await self.send("/proxy off")

        self.assertEqual(await self.db.get_meta("proxy"), "")
        self.assertIn("напрямую", self.bot.texts[-1])

    async def test_vpn_key_is_refused_with_help(self):
        await self.send("/proxy vless://uuid@host:443")

        self.assertIn("не адрес прокси", self.bot.texts[-1])
        self.assertIsNone(await self.db.get_meta("proxy"))
        self.assertFalse(self.restart_event.is_set())


if __name__ == "__main__":
    unittest.main()
