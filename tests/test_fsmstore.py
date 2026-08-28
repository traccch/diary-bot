"""Состояние диалогов переживает перезапуск — иначе обновление рвёт сессию."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from aiogram.fsm.storage.base import StorageKey

from bot.db import Database
from bot.fsmstore import SQLiteStorage

from .support import memory_db

KEY = StorageKey(bot_id=42, chat_id=555, user_id=777)
OTHER = StorageKey(bot_id=42, chat_id=555, user_id=999)


class StorageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = memory_db()
        await self.db.connect()
        self.storage = SQLiteStorage(self.db.conn)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_state_round_trip(self):
        self.assertIsNone(await self.storage.get_state(KEY))

        await self.storage.set_state(KEY, "EngSession:answering")
        self.assertEqual(await self.storage.get_state(KEY), "EngSession:answering")

        await self.storage.set_state(KEY, None)
        self.assertIsNone(await self.storage.get_state(KEY))

    async def test_data_round_trip(self):
        self.assertEqual(await self.storage.get_data(KEY), {})

        await self.storage.set_data(KEY, {"index": 7, "score": 5, "items": ["though"]})
        self.assertEqual(
            await self.storage.get_data(KEY),
            {"index": 7, "score": 5, "items": ["though"]},
        )

        await self.storage.update_data(KEY, {"index": 8})
        self.assertEqual((await self.storage.get_data(KEY))["index"], 8)
        self.assertEqual((await self.storage.get_data(KEY))["score"], 5)

    async def test_people_do_not_mix(self):
        await self.storage.set_state(KEY, "EngSession:answering")
        self.assertIsNone(await self.storage.get_state(OTHER))

    async def test_broken_data_does_not_break_the_bot(self):
        await self.storage.set_data(KEY, {"a": 1})
        await self.db.conn.execute("UPDATE fsm_state SET data = 'не json'")
        await self.db.conn.commit()
        self.assertEqual(await self.storage.get_data(KEY), {})

    async def test_busy_while_the_talk_goes_on(self):
        self.assertFalse(await self.storage.busy())

        await self.storage.set_state(KEY, "EngSession:answering")
        self.assertTrue(await self.storage.busy())

        await self.storage.set_state(KEY, None)
        self.assertFalse(await self.storage.busy())

    async def test_abandoned_talk_is_not_busy(self):
        await self.storage.set_state(KEY, "EngSession:answering")
        await self.db.conn.execute(
            "UPDATE fsm_state SET updated_at = datetime('now', '-2 hours')"
        )
        await self.db.conn.commit()
        self.assertFalse(await self.storage.busy())

    async def test_stale_talks_are_forgotten(self):
        await self.storage.set_state(KEY, "EngSession:answering")
        await self.db.conn.execute(
            "UPDATE fsm_state SET updated_at = datetime('now', '-3 days')"
        )
        await self.db.conn.commit()

        self.assertEqual(await self.storage.forget_stale(), 1)
        self.assertIsNone(await self.storage.get_state(KEY))


class ShiftedClockTest(unittest.IsolatedAsyncioTestCase):
    """Часы базы и часы питона — разные часы.

    Метку времени ставит SQLite, а он пишет UTC. Если сравнивать её с местным
    временем, то в поясе UTC+7 «полчаса назад» окажется в будущем: живых
    разговоров не найдётся никогда, а брошенными окажутся все — включая
    только что начатые. На машине в UTC это незаметно, поэтому проверяем
    нарочно сдвинутыми часами.
    """

    async def test_busy_and_stale_do_not_depend_on_the_local_clock(self):
        import os
        import time

        if not hasattr(time, "tzset"):  # pragma: no cover - Windows
            self.skipTest("часовой пояс процесса меняется только на Unix")

        was = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Krasnoyarsk"
        time.tzset()
        try:
            db = memory_db()
            await db.connect()
            storage = SQLiteStorage(db.conn)
            try:
                await storage.set_state(KEY, "EngSession:answering")
                self.assertTrue(await storage.busy())
                self.assertEqual(await storage.forget_stale(), 0)
                self.assertEqual(
                    await storage.get_state(KEY), "EngSession:answering"
                )
            finally:
                await db.close()
        finally:
            if was is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = was
            time.tzset()


class SurvivesRestartTest(unittest.IsolatedAsyncioTestCase):
    """Главное, ради чего всё затевалось: сессия переживает перезапуск бота."""

    async def test_seventh_question_is_still_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "diary.db")

            first = Database(path, "Europe/Moscow")
            await first.connect()
            storage = SQLiteStorage(first.conn)
            await storage.set_state(KEY, "EngSession:answering")
            await storage.set_data(KEY, {"index": 7, "total": 9, "score": 5})
            await first.close()  # бот обновился и умер

            second = Database(path, "Europe/Moscow")
            await second.connect()
            revived = SQLiteStorage(second.conn)
            try:
                self.assertEqual(
                    await revived.get_state(KEY), "EngSession:answering"
                )
                self.assertEqual((await revived.get_data(KEY))["index"], 7)
            finally:
                await second.close()


class PostponedUpdateTest(unittest.IsolatedAsyncioTestCase):
    """Обновление ждёт, пока человек договорит."""

    async def asyncSetUp(self):
        self.db = memory_db()
        await self.db.connect()
        self.storage = SQLiteStorage(self.db.conn)
        self.sent = []

    async def installer(self):
        from bot.main import make_installer

        class FakeBot:
            def __init__(self, sent):
                self._sent = sent

            async def send_message(self, chat_id, text, **kwargs):
                self._sent.append(text)
                raise AssertionError("до отправки дойти не должно")

        return make_installer(
            FakeBot(self.sent), self.db, None, asyncio.Event(), self.storage
        )

    async def asyncTearDown(self):
        await self.db.close()

    async def test_waits_for_the_talk_to_end(self):
        from bot.updater import UpdateStatus

        await self.storage.set_state(KEY, "EngSession:answering")
        install = await self.installer()
        status = UpdateStatus(branch="main", local="aaa", remote="bbb", behind=1)

        await install(777, status)  # не должно ничего отправить
        self.assertEqual(self.sent, [])

        # и метку «уже сообщали» снимаем, чтобы вернуться к обновлению позже
        self.assertEqual(await self.db.get_meta("notified_commit"), "")


if __name__ == "__main__":
    unittest.main()
