"""Хранилище: измерения, настройки, напоминания."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from bot.db import Database

USER_ID = 777
NOW = dt.datetime(2026, 8, 17, 9, 0)


class DatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "test.db"), "Europe/Moscow")
        await self.db.connect()
        self.user = await self.db.ensure_user(USER_ID)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def add(self, systolic, diastolic, pulse=None, moment=NOW, note=""):
        return await self.db.add_measurement(
            USER_ID, systolic, diastolic, pulse, moment, note
        )

    # ------------------------------------------------------------ настройки

    async def test_defaults(self):
        self.assertEqual(self.user.tz, "Europe/Moscow")
        self.assertEqual((self.user.target_sys, self.user.target_dia), (135, 85))
        self.assertTrue(self.user.skip_if_measured)

    async def test_ensure_user_is_idempotent(self):
        again = await self.db.ensure_user(USER_ID)
        self.assertEqual(again, self.user)

    async def test_settings_roundtrip(self):
        await self.db.set_tz(USER_ID, "Asia/Almaty")
        await self.db.set_target(USER_ID, 130, 80)
        await self.db.set_skip_if_measured(USER_ID, False)
        settings = await self.db.ensure_user(USER_ID)
        self.assertEqual(settings.tz, "Asia/Almaty")
        self.assertEqual((settings.target_sys, settings.target_dia), (130, 80))
        self.assertFalse(settings.skip_if_measured)

    # ----------------------------------------------------------- измерения

    async def test_add_and_read(self):
        created = await self.add(128, 84, 66, note="после прогулки")
        stored = await self.db.get_measurement(USER_ID, created.id)
        self.assertEqual(stored, created)
        self.assertEqual(stored.bp, "128/84")
        self.assertEqual(stored.measured_at, NOW)
        self.assertEqual(stored.note, "после прогулки")

    async def test_data_is_isolated_between_users(self):
        created = await self.add(120, 80)
        await self.db.ensure_user(USER_ID + 1)
        self.assertIsNone(await self.db.get_measurement(USER_ID + 1, created.id))
        self.assertEqual(await self.db.last_measurements(USER_ID + 1), [])

    async def test_seconds_are_not_stored(self):
        created = await self.add(120, 80, moment=dt.datetime(2026, 8, 17, 9, 0, 45))
        self.assertEqual(created.measured_at, dt.datetime(2026, 8, 17, 9, 0))

    async def test_last_measurements_are_newest_first(self):
        await self.add(120, 80, moment=NOW - dt.timedelta(days=2))
        await self.add(130, 85, moment=NOW)
        await self.add(125, 82, moment=NOW - dt.timedelta(days=1))
        order = [m.bp for m in await self.db.last_measurements(USER_ID)]
        self.assertEqual(order, ["130/85", "125/82", "120/80"])

    async def test_between_is_inclusive_and_sorted(self):
        for day in range(5):
            await self.add(120 + day, 80, moment=NOW - dt.timedelta(days=day))
        window = await self.db.measurements_between(
            USER_ID, NOW - dt.timedelta(days=2), NOW
        )
        self.assertEqual([m.systolic for m in window], [122, 121, 120])

    async def test_delete_and_undo(self):
        created = await self.add(120, 80)
        self.assertTrue(await self.db.delete_measurement(USER_ID, created.id))
        self.assertFalse(await self.db.delete_measurement(USER_ID, created.id))
        self.assertEqual(await self.db.count_measurements(USER_ID), 0)

    async def test_note_update(self):
        created = await self.add(120, 80)
        updated = await self.db.set_note(USER_ID, created.id, "  перед сном  ")
        self.assertEqual(updated.note, "перед сном")
        self.assertIsNone(await self.db.set_note(USER_ID, 999, "нет такого"))

    async def test_first_measured_at(self):
        self.assertIsNone(await self.db.first_measured_at(USER_ID))
        await self.add(120, 80, moment=NOW)
        await self.add(120, 80, moment=NOW - dt.timedelta(days=10))
        self.assertEqual(
            await self.db.first_measured_at(USER_ID), NOW - dt.timedelta(days=10)
        )

    async def test_has_measurement_since(self):
        await self.add(120, 80, moment=NOW - dt.timedelta(hours=3))
        self.assertFalse(
            await self.db.has_measurement_since(USER_ID, NOW - dt.timedelta(hours=1))
        )
        self.assertTrue(
            await self.db.has_measurement_since(USER_ID, NOW - dt.timedelta(hours=5))
        )

    # --------------------------------------------------------- напоминания

    async def test_snooze_keeps_the_topic(self):
        await self.db.add_snooze(USER_ID, NOW, "money")
        self.assertEqual(await self.db.pop_due_snoozes(NOW), [(USER_ID, "money")])

    async def test_reminders_crud(self):
        # напоминания по умолчанию проверяются отдельно, здесь они мешают
        await self.db.delete_all_reminders(USER_ID)
        morning = await self.db.add_reminder(USER_ID, dt.time(8, 0))
        evening = await self.db.add_reminder(USER_ID, dt.time(21, 0))
        self.assertIsNotNone(morning)
        self.assertEqual(morning.label, "08:00")
        self.assertIsNone(await self.db.add_reminder(USER_ID, dt.time(8, 0)))

        labels = [r.label for r in await self.db.list_reminders(USER_ID)]
        self.assertEqual(labels, ["08:00", "21:00"])

        self.assertTrue(await self.db.delete_reminder(USER_ID, evening.at))
        self.assertFalse(await self.db.delete_reminder(USER_ID, evening.at))
        self.assertEqual(await self.db.delete_all_reminders(USER_ID), 1)
        self.assertEqual(await self.db.list_reminders(USER_ID), [])

    async def test_due_candidates_carry_owner_settings(self):
        # напоминания по умолчанию проверяются отдельно, здесь они мешают
        await self.db.delete_all_reminders(USER_ID)
        await self.db.add_reminder(USER_ID, dt.time(8, 0))
        await self.db.set_tz(USER_ID, "Asia/Almaty")
        candidates = await self.db.due_candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].tz, "Asia/Almaty")
        self.assertEqual(candidates[0].at, dt.time(8, 0))
        self.assertIsNone(candidates[0].last_fired_on)

    async def test_mark_fired(self):
        # напоминания по умолчанию проверяются отдельно, здесь они мешают
        await self.db.delete_all_reminders(USER_ID)
        reminder = await self.db.add_reminder(USER_ID, dt.time(8, 0))
        await self.db.mark_reminder_fired(reminder.id, NOW.date())
        candidates = await self.db.due_candidates()
        self.assertEqual(candidates[0].last_fired_on, NOW.date())

    async def test_snoozes_pop_once(self):
        await self.db.add_snooze(USER_ID, NOW)
        await self.db.add_snooze(USER_ID, NOW + dt.timedelta(hours=1))
        self.assertEqual(await self.db.pop_due_snoozes(NOW), [(USER_ID, "pressure")])
        self.assertEqual(await self.db.pop_due_snoozes(NOW), [])
        self.assertEqual(
            await self.db.pop_due_snoozes(NOW + dt.timedelta(hours=2)),
            [(USER_ID, "pressure")],
        )


if __name__ == "__main__":
    unittest.main()


class MigrationTest(unittest.IsolatedAsyncioTestCase):
    """Старая база должна дополняться, а не падать с «no such column»."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "old.db")

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def make_old_db(self) -> None:
        """База прошлой версии: в users нет ни валюты, ни раздела, ни пометки."""
        import aiosqlite

        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "CREATE TABLE users ("
                " user_id INTEGER PRIMARY KEY,"
                " tz TEXT NOT NULL DEFAULT 'Europe/Moscow',"
                " target_sys INTEGER NOT NULL DEFAULT 135,"
                " target_dia INTEGER NOT NULL DEFAULT 85,"
                " skip_if_measured INTEGER NOT NULL DEFAULT 1,"
                " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            await conn.execute("INSERT INTO users (user_id, tz) VALUES (?, ?)", (777, "Asia/Omsk"))
            await conn.commit()

    async def test_missing_columns_are_added(self):
        await self.make_old_db()

        db = Database(self.path, "Europe/Moscow")
        await db.connect()
        try:
            cur = await db.conn.execute("PRAGMA table_info(users)")
            columns = {row["name"] for row in await cur.fetchall()}
            self.assertIn("currency", columns)
            self.assertIn("section", columns)
            self.assertIn("reminders_seeded", columns)

            # и запись из старой базы на месте, с прежним часовым поясом
            user = await db.ensure_user(777)
            self.assertEqual(user.tz, "Asia/Omsk")
        finally:
            await db.close()

    async def test_new_tables_appear_too(self):
        await self.make_old_db()

        db = Database(self.path, "Europe/Moscow")
        await db.connect()
        try:
            # разделы, которых в той версии не было вовсе
            await db.set_reading(777, dt.date(2026, 8, 28), 203116)
            self.assertEqual((await db.last_reading(777)).km, 203116)
        finally:
            await db.close()

    async def test_fresh_database_needs_no_migration(self):
        db = Database(self.path, "Europe/Moscow")
        await db.connect()
        try:
            user = await db.ensure_user(777)
            self.assertEqual(user.currency, "₽")
        finally:
            await db.close()
