"""Кто может писать боту: личный дневник закрыт для посторонних."""

from __future__ import annotations

import datetime as dt
import unittest

from aiogram.types import Chat, Message, User

from bot.config import read_allowed
from bot.middlewares import AccessMiddleware

from .support import memory_db

OWNER = 777
STRANGER = 999


def message(user_id: int) -> Message:
    return Message(
        message_id=1,
        date=dt.datetime.now(dt.timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Кто-то"),
        text="привет",
    )


class ReadAllowedTest(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(read_allowed("123, 456", None), frozenset({123, 456}))
        self.assertEqual(read_allowed("123 456", None), frozenset({123, 456}))
        self.assertEqual(read_allowed("", None), frozenset())

    def test_owner_is_always_in(self):
        self.assertEqual(read_allowed("123", 777), frozenset({123, 777}))

    def test_garbage_is_dropped(self):
        self.assertEqual(read_allowed("123, абв, ", None), frozenset({123}))


class AccessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = memory_db()
        await self.db.connect()
        self.calls: list[int] = []

    async def asyncTearDown(self):
        await self.db.close()

    async def handler(self, event, data):
        self.calls.append(data["db"] is self.db)
        return "прошёл"

    async def run_for(self, middleware, user_id: int):
        return await middleware(self.handler, message(user_id), {"db": self.db})

    async def test_first_user_becomes_the_owner(self):
        middleware = AccessMiddleware()
        self.assertEqual(await self.run_for(middleware, OWNER), "прошёл")

        await self.db.ensure_user(OWNER)  # хозяин записан
        self.assertIsNone(await self.run_for(middleware, STRANGER))
        self.assertEqual(len(self.calls), 1)

    async def test_explicit_list_decides(self):
        middleware = AccessMiddleware({OWNER})
        await self.db.ensure_user(STRANGER)  # он даже написал первым

        self.assertIsNone(await self.run_for(middleware, STRANGER))
        self.assertEqual(await self.run_for(middleware, OWNER), "прошёл")

    async def test_second_allowed_user(self):
        """Жену можно добавить, не открывая бота всему миру."""
        middleware = AccessMiddleware({OWNER, 555})
        self.assertEqual(await self.run_for(middleware, 555), "прошёл")
        self.assertIsNone(await self.run_for(middleware, STRANGER))

    async def test_stranger_leaves_no_trace(self):
        middleware = AccessMiddleware({OWNER})
        await self.run_for(middleware, STRANGER)

        cur = await self.db.conn.execute("SELECT COUNT(*) AS n FROM users")
        self.assertEqual((await cur.fetchone())["n"], 0)

    async def test_stranger_is_told_once(self):
        middleware = AccessMiddleware({OWNER})
        with self.assertLogs("bot.middlewares", level="WARNING") as logged:
            await self.run_for(middleware, STRANGER)
            await self.run_for(middleware, STRANGER)

        # в лог пишем каждый раз — по нему видно чужой id
        self.assertEqual(len(logged.output), 2)
        self.assertIn(str(STRANGER), logged.output[0])


if __name__ == "__main__":
    unittest.main()
