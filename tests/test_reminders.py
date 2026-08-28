"""Планировщик напоминаний: когда пора, когда молчим, когда откладываем."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest

from bot import sections
from bot.db import DEFAULT_REMINDERS
from bot.reminders import ReminderScheduler, is_due, local_now, next_fire, wait_text

from .support import memory_db

USER_ID = 777
# 05:00 UTC — это 08:00 в Москве и 10:00 в Алматы
NOW_UTC = dt.datetime(2026, 8, 17, 5, 0, tzinfo=dt.timezone.utc)


class FakeBot:
    """Вместо HTTP-запроса складывает сообщения в список."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        self.sent.append((chat_id, text))


class IsDueTest(unittest.TestCase):
    def check(self, hour, minute, last_fired=None, at=dt.time(8, 0)):
        return is_due(dt.datetime(2026, 8, 17, hour, minute), at, last_fired)

    def test_exactly_on_time(self):
        self.assertTrue(self.check(8, 0))

    def test_slightly_late_still_counts(self):
        self.assertTrue(self.check(8, 15))

    def test_too_late_is_skipped(self):
        self.assertFalse(self.check(9, 30))

    def test_before_time(self):
        self.assertFalse(self.check(7, 59))

    def test_already_fired_today(self):
        self.assertFalse(self.check(8, 5, last_fired=dt.date(2026, 8, 17)))

    def test_fired_yesterday_does_not_block(self):
        self.assertTrue(self.check(8, 5, last_fired=dt.date(2026, 8, 16)))


class LocalNowTest(unittest.TestCase):
    def test_timezones(self):
        self.assertEqual(local_now("Europe/Moscow", NOW_UTC).hour, 8)
        self.assertEqual(local_now("Asia/Almaty", NOW_UTC).hour, 10)

    def test_broken_timezone_falls_back_to_utc(self):
        self.assertEqual(local_now("Мордор/Барад-Дур", NOW_UTC).hour, 5)


class NextFireTest(unittest.TestCase):
    """Ближайшее напоминание — для шапки в консоли."""

    def setUp(self):
        self.now = dt.datetime(2026, 8, 27, 23, 15)

    def test_nearest_time_tomorrow(self):
        at, wait = next_fire([dt.time(8, 0), dt.time(21, 30)], self.now)
        self.assertEqual(at, dt.time(8, 0))
        self.assertEqual(wait_text(wait), "через 8 ч 45 мин")

    def test_nearest_time_today(self):
        at, wait = next_fire([dt.time(8, 0), dt.time(23, 40)], self.now)
        self.assertEqual(at, dt.time(23, 40))
        self.assertEqual(wait_text(wait), "через 25 мин")

    def test_no_reminders(self):
        self.assertIsNone(next_fire([], self.now))

    def test_round_hour_has_no_dangling_minutes(self):
        _, wait = next_fire([dt.time(2, 15)], self.now)
        self.assertEqual(wait_text(wait), "через 3 ч")


class SchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = memory_db()
        await self.db.connect()
        await self.db.ensure_user(USER_ID)
        # По умолчанию бот ставит напоминания сам — в этих тестах они бы
        # срабатывали фоном и путали счёт. Умолчания проверяет test_english.
        await self.db.delete_all_reminders(USER_ID)
        self.bot = FakeBot()
        self.scheduler = ReminderScheduler(self.bot, self.db)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def test_sends_once_per_day(self):
        await self.db.add_reminder(USER_ID, dt.time(8, 0))
        self.assertEqual(await self.scheduler.tick(NOW_UTC), 1)
        self.assertIn("Пора измерить давление", self.bot.sent[0][1])

        # следующий тик через минуту — второго сообщения быть не должно
        self.assertEqual(
            await self.scheduler.tick(NOW_UTC + dt.timedelta(minutes=1)), 0
        )
        self.assertEqual(len(self.bot.sent), 1)

        # на следующий день — снова
        self.assertEqual(await self.scheduler.tick(NOW_UTC + dt.timedelta(days=1)), 1)

    async def test_respects_user_timezone(self):
        await self.db.set_tz(USER_ID, "Asia/Almaty")
        await self.db.add_reminder(USER_ID, dt.time(8, 0))
        # в Алматы уже 10:00 — окно ожидания прошло
        self.assertEqual(await self.scheduler.tick(NOW_UTC), 0)

        await self.db.delete_all_reminders(USER_ID)
        await self.db.add_reminder(USER_ID, dt.time(10, 0))
        self.assertEqual(await self.scheduler.tick(NOW_UTC), 1)

    async def test_silent_if_measured_recently(self):
        await self.db.add_reminder(USER_ID, dt.time(8, 0))
        await self.db.add_measurement(
            USER_ID, 120, 80, 65, dt.datetime(2026, 8, 17, 7, 30)
        )
        self.assertEqual(await self.scheduler.tick(NOW_UTC), 0)

    async def test_old_measurement_does_not_silence(self):
        await self.db.add_reminder(USER_ID, dt.time(8, 0))
        await self.db.add_measurement(
            USER_ID, 120, 80, 65, dt.datetime(2026, 8, 16, 21, 0)
        )
        self.assertEqual(await self.scheduler.tick(NOW_UTC), 1)

    async def test_skip_can_be_switched_off(self):
        await self.db.set_skip_if_measured(USER_ID, False)
        await self.db.add_reminder(USER_ID, dt.time(8, 0))
        await self.db.add_measurement(
            USER_ID, 120, 80, 65, dt.datetime(2026, 8, 17, 7, 30)
        )
        self.assertEqual(await self.scheduler.tick(NOW_UTC), 1)

    async def test_disabled_reminder_stays_quiet(self):
        await self.db.add_reminder(USER_ID, dt.time(8, 0))
        await self.db.delete_all_reminders(USER_ID)
        self.assertEqual(await self.scheduler.tick(NOW_UTC), 0)

    async def test_snooze_fires_when_due(self):
        naive_utc = NOW_UTC.replace(tzinfo=None)
        await self.db.add_snooze(USER_ID, naive_utc + dt.timedelta(minutes=15))
        self.assertEqual(await self.scheduler.tick(NOW_UTC), 0)
        self.assertEqual(
            await self.scheduler.tick(NOW_UTC + dt.timedelta(minutes=15)), 1
        )
        self.assertIn("ещё раз", self.bot.sent[0][1])

    async def test_send_failure_does_not_break_the_tick(self):
        from aiogram.exceptions import TelegramAPIError

        class BrokenBot(FakeBot):
            async def send_message(self, chat_id, text, reply_markup=None):
                raise TelegramAPIError(method=None, message="user blocked the bot")

        scheduler = ReminderScheduler(BrokenBot(), self.db)
        await self.db.add_reminder(USER_ID, dt.time(8, 0))
        self.assertEqual(await scheduler.tick(NOW_UTC), 0)


if __name__ == "__main__":
    unittest.main()


class SeedingTest(unittest.IsolatedAsyncioTestCase):
    """Умолчания по темам: новая тема доезжает, выключенная не возвращается."""

    async def asyncSetUp(self):
        self.db = memory_db()
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    async def topics(self, user_id: int = USER_ID) -> set:
        return {item.topic for item in await self.db.list_reminders(user_id)}

    async def test_new_user_gets_everything(self):
        await self.db.ensure_user(USER_ID)
        self.assertEqual(await self.topics(), set(DEFAULT_REMINDERS))

    async def test_deleted_reminders_do_not_come_back(self):
        await self.db.ensure_user(USER_ID)
        await self.db.delete_all_reminders(USER_ID, sections.ENGLISH)

        await self.db.ensure_user(USER_ID)
        self.assertNotIn(sections.ENGLISH, await self.topics())

    async def test_new_topic_reaches_an_old_diary(self):
        """Дневник, заведённый до появления темы, должен её получить."""
        await self.db.ensure_user(USER_ID)
        # изображаем старую базу: тема ещё не существовала
        await self.db.delete_all_reminders(USER_ID, sections.CAR)
        await self.db.conn.execute(
            "UPDATE users SET seeded_topics = '' WHERE user_id = ?", (USER_ID,)
        )
        await self.db.conn.commit()

        await self.db.ensure_user(USER_ID)
        self.assertIn(sections.CAR, await self.topics())

    async def test_seeding_is_idempotent(self):
        await self.db.ensure_user(USER_ID)
        before = len(await self.db.list_reminders(USER_ID))
        for _ in range(3):
            await self.db.ensure_user(USER_ID)
        self.assertEqual(len(await self.db.list_reminders(USER_ID)), before)
