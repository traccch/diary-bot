"""Раздел английского: материал, интервалы, прогресс и напоминания по умолчанию."""

from __future__ import annotations

import datetime as dt
import random
import tempfile
import unittest
from pathlib import Path

from bot import sections
from bot.db import DEFAULT_REMINDERS, Database
from bot.english import content, quests, srs
from bot.english.db import streak
from bot.english.handlers.progress import level_of
from bot.english.lookup import find, looks_english

from .support import memory_db

USER_ID = 777
TODAY = dt.date(2026, 8, 27)


class ContentTest(unittest.TestCase):
    def test_cards_are_well_formed(self):
        self.assertGreater(len(content.CARDS), 100)
        self.assertEqual(len(content.BY_ID), len(content.CARDS), "id должны быть уникальны")

        for card in content.CARDS:
            with self.subTest(card=card.id):
                self.assertTrue(card.en and card.ru and card.example)
                self.assertIn(card.pack, content.PACK_BY_KEY)
                self.assertIn(card.level, (1, 2, 3))
                self.assertTrue(card.example_ru, "у примера должен быть перевод")

    def test_every_pack_has_cards(self):
        for pack in content.PACKS:
            with self.subTest(pack=pack.key):
                self.assertTrue(content.cards_of_pack(pack.key))

    def test_quests_are_answerable(self):
        self.assertTrue(quests.QUESTS)
        for quest in quests.QUESTS:
            with self.subTest(quest=quest.id):
                self.assertTrue(quest.scene and quest.vocab)
                for question in quest.questions:
                    self.assertGreaterEqual(len(question.options), 2)
                    self.assertTrue(0 <= question.correct < len(question.options))

    def test_next_quest_follows_order(self):
        self.assertEqual(quests.next_quest([]).id, quests.QUESTS[0].id)
        self.assertEqual(
            quests.next_quest([quests.QUESTS[0].id]).id, quests.QUESTS[1].id
        )
        self.assertIsNone(quests.next_quest([quest.id for quest in quests.QUESTS]))


class SrsTest(unittest.TestCase):
    def test_correct_answer_moves_forward(self):
        self.assertEqual(srs.next_box(0, True), 1)
        self.assertEqual(srs.next_box(3, True), 4)

    def test_mistake_returns_to_the_start(self):
        self.assertEqual(srs.next_box(5, False), 0)

    def test_box_does_not_grow_past_the_last_interval(self):
        top = len(srs.INTERVALS) - 1
        self.assertEqual(srs.next_box(top, True), top)

    def test_intervals_grow(self):
        self.assertEqual(srs.due_after(0, TODAY), TODAY + dt.timedelta(days=1))
        self.assertGreater(srs.due_after(3, TODAY), srs.due_after(1, TODAY))

    def test_question_has_one_right_option(self):
        rng = random.Random(1)
        card = content.BY_ID["games:loot"]
        for kind in (srs.RECOGNIZE, srs.RECALL, srs.CLOZE):
            with self.subTest(kind=kind):
                question = srs.make_question(card, kind, rng)
                self.assertEqual(len(question.options), 4)
                self.assertEqual(len(set(question.options)), 4, "варианты не повторяются")
                right = question.options[question.correct]
                self.assertEqual(right, card.ru if kind == srs.RECOGNIZE else card.en)

    def test_cloze_hides_the_word(self):
        question = srs.make_question(
            content.BY_ID["games:loot"], srs.CLOZE, random.Random(2)
        )
        self.assertNotIn("loot", question.prompt.lower().replace("какое слово", ""))

    def test_new_word_starts_with_recognition(self):
        self.assertEqual(srs.kind_for(None), srs.RECOGNIZE)

    def test_session_takes_due_first(self):
        progress = [
            srs.Progress("core:keep", 2, TODAY - dt.timedelta(days=1), 5, 4, 2),
            srs.Progress("core:still", 1, TODAY + dt.timedelta(days=3), 2, 2, 0),
        ]
        queue = srs.build_session(progress, TODAY, size=5, rng=random.Random(3))
        self.assertIn("core:keep", queue)
        self.assertNotIn("core:still", queue, "не пришёл срок — не показываем")

    def test_session_limits_new_words(self):
        queue = srs.build_session([], TODAY, size=20, new_per_day=4, rng=random.Random(4))
        self.assertEqual(len(queue), 4)

    def test_daily_new_limit_counts_what_was_today(self):
        queue = srs.build_session(
            [], TODAY, size=20, new_per_day=6, new_today=6, rng=random.Random(5)
        )
        self.assertEqual(queue, [])

    def test_forgotten_words_come_first(self):
        progress = [
            srs.Progress("core:keep", 1, TODAY, 9, 3, 5),
            srs.Progress("core:wait", 1, TODAY, 4, 4, 0),
        ]
        queue = srs.build_session(progress, TODAY, size=2, new_per_day=0)
        self.assertEqual(queue[0], "core:keep")


class StreakTest(unittest.TestCase):
    def test_days_in_a_row(self):
        days = [TODAY, TODAY - dt.timedelta(days=1), TODAY - dt.timedelta(days=2)]
        self.assertEqual(streak(days, TODAY), 3)

    def test_yesterday_keeps_the_streak_alive(self):
        self.assertEqual(streak([TODAY - dt.timedelta(days=1)], TODAY), 1)

    def test_a_gap_breaks_it(self):
        self.assertEqual(streak([TODAY - dt.timedelta(days=3)], TODAY), 0)
        self.assertEqual(streak([], TODAY), 0)

    def test_gap_inside_stops_counting(self):
        days = [TODAY, TODAY - dt.timedelta(days=1), TODAY - dt.timedelta(days=5)]
        self.assertEqual(streak(days, TODAY), 2)


class LookupTest(unittest.TestCase):
    def test_recognises_english(self):
        self.assertTrue(looks_english("loot"))
        self.assertTrue(looks_english("hold on"))
        self.assertFalse(looks_english("кофе 300"))
        self.assertFalse(looks_english("120/80"))

    def test_finds_exact_and_partial(self):
        self.assertEqual(find("loot")[0].id, "games:loot")
        self.assertEqual(find("LOOT")[0].id, "games:loot")
        self.assertTrue(find("give"))
        self.assertEqual(find("щщщ"), [])


class LevelTest(unittest.TestCase):
    def test_level_grows_with_words(self):
        self.assertIn("начало", level_of(0))
        self.assertIn("A1", level_of(60))
        self.assertIn("B1", level_of(200))


class ProgressStorageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = memory_db()
        await self.db.connect()
        await self.db.ensure_user(USER_ID)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def test_answer_is_saved_and_accumulated(self):
        await self.db.eng_save_answer(USER_ID, "core:keep", 1, TODAY, True, False)
        await self.db.eng_save_answer(USER_ID, "core:keep", 0, TODAY, False, True)

        stored = await self.db.eng_progress_of(USER_ID, "core:keep")
        self.assertEqual((stored.box, stored.seen, stored.correct, stored.lapses), (0, 2, 1, 1))

    async def test_counts_learned(self):
        await self.db.eng_save_answer(USER_ID, "core:keep", srs.LEARNED_BOX, TODAY, True, False)
        await self.db.eng_save_answer(USER_ID, "core:wait", 1, TODAY, True, False)

        total, learned = await self.db.eng_counts(USER_ID)
        self.assertEqual((total, learned), (2, 1))

    async def test_due_count(self):
        await self.db.eng_save_answer(USER_ID, "core:keep", 1, TODAY, True, False)
        await self.db.eng_save_answer(
            USER_ID, "core:wait", 1, TODAY + dt.timedelta(days=5), True, False
        )
        self.assertEqual(await self.db.eng_due_count(USER_ID, TODAY), 1)

    async def test_day_stats_and_practice_flag(self):
        self.assertFalse(await self.db.eng_practiced_since(USER_ID, TODAY))

        await self.db.eng_bump_day(USER_ID, TODAY, correct=True, is_new=True)
        await self.db.eng_bump_day(USER_ID, TODAY, correct=False, is_new=False)

        day = await self.db.eng_day(USER_ID, TODAY)
        self.assertEqual((day.answered, day.correct, day.new_seen), (2, 1, 1))
        self.assertTrue(await self.db.eng_practiced_since(USER_ID, TODAY))

    async def test_quests_are_remembered_with_best_score(self):
        await self.db.eng_finish_quest(USER_ID, "tavern", TODAY, 2)
        await self.db.eng_finish_quest(USER_ID, "tavern", TODAY, 3)
        self.assertEqual(await self.db.eng_done_quests(USER_ID), ["tavern"])


class DefaultRemindersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "test.db")
        self.db = Database(self.path, "Europe/Moscow")
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def test_new_user_gets_reminders_without_asking(self):
        await self.db.ensure_user(USER_ID)
        reminders = await self.db.list_reminders(USER_ID)

        expected = sum(len(times) for times in DEFAULT_REMINDERS.values())
        self.assertEqual(len(reminders), expected)
        self.assertEqual(
            {item.topic for item in reminders},
            {
                sections.PRESSURE,
                sections.MONEY,
                sections.ENGLISH,
                sections.HEALTH,
                sections.CAR,
            },
        )

    async def test_seeding_happens_once(self):
        await self.db.ensure_user(USER_ID)
        before = len(await self.db.list_reminders(USER_ID))
        await self.db.ensure_user(USER_ID)
        self.assertEqual(len(await self.db.list_reminders(USER_ID)), before)

    async def test_switched_off_stays_off(self):
        await self.db.ensure_user(USER_ID)
        await self.db.delete_all_reminders(USER_ID)

        await self.db.ensure_user(USER_ID)
        self.assertEqual(await self.db.list_reminders(USER_ID), [])

    async def test_existing_user_gets_them_on_next_start(self):
        """У кого база заведена прошлой версией — напоминания появятся сами."""
        await self.db.conn.execute(
            "INSERT INTO users (user_id, tz, reminders_seeded) VALUES (?, ?, 0)",
            (USER_ID + 1, "Europe/Moscow"),
        )
        await self.db.conn.commit()

        await self.db.ensure_user(USER_ID + 1)
        self.assertTrue(await self.db.list_reminders(USER_ID + 1))


if __name__ == "__main__":
    unittest.main()
