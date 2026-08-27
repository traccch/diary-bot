"""Мягкие вопросы про самочувствие: что спрашиваем, когда молчим, как пишем."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from bot import prompts, sections
from bot.db import DEFAULT_REMINDERS, Database
from bot.reminders import ReminderScheduler

from .test_handlers import BotTestCase

USER_ID = 777
MONDAY = dt.date(2026, 8, 17)
MORNING = dt.time(9, 30)
EVENING = dt.time(22, 0)
# 06:30 UTC — это 09:30 в Москве
MORNING_UTC = dt.datetime(2026, 8, 17, 6, 30, tzinfo=dt.timezone.utc)
EVENING_UTC = dt.datetime(2026, 8, 17, 19, 0, tzinfo=dt.timezone.utc)


class PickTest(unittest.TestCase):
    def test_morning_asks_about_sleep(self):
        self.assertIs(prompts.pick(MORNING, MONDAY), prompts.SLEEP)
        self.assertIs(prompts.pick(dt.time(11, 59), MONDAY), prompts.SLEEP)

    def test_evening_rotates_over_the_week(self):
        picked = [
            prompts.pick(EVENING, MONDAY + dt.timedelta(days=day)).kind
            for day in range(7)
        ]
        self.assertEqual(picked.count("steps"), 4)
        self.assertEqual(picked.count("resting_pulse"), 2)
        self.assertEqual(picked.count("weight"), 1)

    def test_recorded_metric_is_not_asked_again(self):
        # шаги уже записаны — вечером спросим про что-нибудь другое
        other = prompts.pick(EVENING, MONDAY, already=("steps",))
        self.assertNotEqual(other.kind, "steps")

    def test_nothing_left_to_ask(self):
        self.assertIsNone(prompts.pick(MORNING, MONDAY, already=("sleep",)))
        self.assertIsNone(
            prompts.pick(EVENING, MONDAY, already=("steps", "resting_pulse", "weight"))
        )

    def test_choices_are_human_labels(self):
        labels = [choice.label for choice in prompts.SLEEP.choices]
        self.assertIn("7 ч", labels)
        self.assertIn("6:30", labels)
        # сон хранится в минутах — кнопка «7 ч» должна нести 420
        self.assertEqual(prompts.SLEEP.choices[labels.index("7 ч")].value, 420)

    def test_weight_has_no_buttons(self):
        """Шаг в сто граммов кнопками не выбрать — только промахнуться."""
        self.assertEqual(prompts.WEIGHT.choices, ())

    def test_clean_rejects_nonsense(self):
        self.assertEqual(prompts.clean("steps", "8000"), 8000)
        self.assertIsNone(prompts.clean("steps", "миллион"))
        self.assertIsNone(prompts.clean("resting_pulse", "900"))
        self.assertIsNone(prompts.clean("вес-в-фунтах", "70"))

    def test_confirm_speaks_human(self):
        self.assertIn("7 ч", prompts.confirm("sleep", 420))
        self.assertIn("8 000", prompts.confirm("steps", 8000))


class RecordingBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        self.sent.append((chat_id, text, reply_markup))

    @property
    def buttons(self) -> list[str]:
        markup = self.sent[-1][2]
        return [button.text for row in markup.inline_keyboard for button in row]


class SchedulerHealthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "test.db"), "Europe/Moscow")
        await self.db.connect()
        await self.db.ensure_user(USER_ID)
        await self.db.delete_all_reminders(USER_ID)
        self.bot = RecordingBot()
        self.scheduler = ReminderScheduler(self.bot, self.db)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def test_morning_question_with_buttons(self):
        await self.db.add_reminder(USER_ID, MORNING, sections.HEALTH)
        self.assertEqual(await self.scheduler.tick(MORNING_UTC), 1)

        text = self.bot.sent[0][1]
        self.assertIn("Как спалось", text)
        self.assertIn("7 ч", self.bot.buttons)
        self.assertIn("⏭ Не сегодня", self.bot.buttons)

    async def test_evening_question_is_about_the_day(self):
        await self.db.add_reminder(USER_ID, EVENING, sections.HEALTH)
        self.assertEqual(await self.scheduler.tick(EVENING_UTC), 1)
        self.assertIn("прошёл", self.bot.sent[0][1])

    async def test_silence_when_already_recorded(self):
        await self.db.set_metric(USER_ID, "sleep", MONDAY, 420)
        await self.db.add_reminder(USER_ID, MORNING, sections.HEALTH)
        self.assertEqual(await self.scheduler.tick(MORNING_UTC), 0)
        self.assertEqual(self.bot.sent, [])

    async def test_recorded_metric_shifts_the_question(self):
        await self.db.set_metric(USER_ID, "steps", MONDAY, 8000)
        await self.db.add_reminder(USER_ID, EVENING, sections.HEALTH)
        await self.scheduler.tick(EVENING_UTC)
        self.assertIn("Пульс покоя", self.bot.sent[0][1])

    async def test_snooze_asks_again(self):
        naive = EVENING_UTC.replace(tzinfo=None)
        await self.db.add_snooze(USER_ID, naive, sections.HEALTH)
        self.assertEqual(await self.scheduler.tick(EVENING_UTC), 1)
        self.assertIn("прошёл", self.bot.sent[0][1])

    async def test_defaults_include_health(self):
        self.assertIn(sections.HEALTH, DEFAULT_REMINDERS)
        await self.db.seed_default_reminders(USER_ID)
        topics = {item.topic for item in await self.db.list_reminders(USER_ID)}
        self.assertIn(sections.HEALTH, topics)


class HealthButtonsTest(BotTestCase):
    async def test_answer_is_saved_and_confirmed(self):
        await self.click("hm:sleep:420")
        self.assertIn("7 ч", self.bot.edits[-1])

        stored = await self.db.get_metric(USER_ID, "sleep", self.today())
        self.assertEqual(stored.value, 420)

    async def test_steps_button(self):
        await self.click("hm:steps:8000")
        stored = await self.db.get_metric(USER_ID, "steps", self.today())
        self.assertEqual(stored.value, 8000)

    async def test_skip_saves_nothing(self):
        await self.click("hm:skip")
        self.assertIn("не сегодня", self.bot.edits[-1].lower())
        self.assertEqual(await self.db.count_metrics(USER_ID), 0)

    async def test_broken_value_is_not_saved(self):
        await self.click("hm:steps:миллион")
        self.assertEqual(await self.db.count_metrics(USER_ID), 0)

    def today(self) -> dt.date:
        from bot.middlewares import now_for

        return now_for("Europe/Moscow").date()


if __name__ == "__main__":
    unittest.main()
