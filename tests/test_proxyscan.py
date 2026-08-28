"""Поиск локального прокси: бот ищет порт сам, чтобы человек не искал."""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from bot import proxyscan
from bot.config import read_proxy


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


if __name__ == "__main__":
    unittest.main()
