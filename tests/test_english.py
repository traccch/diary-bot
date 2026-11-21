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
from bot.english.handlers.session import milestones
from bot.english.lookup import find, looks_english, parse_entry

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


class OwnWordsParseTest(unittest.TestCase):
    def test_word_and_translation(self):
        self.assertEqual(parse_entry("loot — добыча"), ("loot", "добыча", ""))
        self.assertEqual(parse_entry("loot - добыча"), ("loot", "добыча", ""))
        self.assertEqual(parse_entry("loot: добыча"), ("loot", "добыча", ""))
        self.assertEqual(parse_entry("I'm down = я за"), ("I'm down", "я за", ""))

    def test_example_after_semicolon_or_newline(self):
        self.assertEqual(
            parse_entry("loot — добыча; We split the loot."),
            ("loot", "добыча", "We split the loot."),
        )
        self.assertEqual(
            parse_entry("respawn — возродиться\nYou will respawn in 5"),
            ("respawn", "возродиться", "You will respawn in 5"),
        )

    def test_hyphen_inside_word_is_kept(self):
        self.assertEqual(parse_entry("check-in - регистрация")[0], "check-in")

    def test_not_a_word_entry(self):
        self.assertIsNone(parse_entry("loot"))
        self.assertIsNone(parse_entry("taxi — 300"), "это трата, а не слово")
        self.assertIsNone(parse_entry("кофе — 300"))
        self.assertIsNone(parse_entry("120/80 68"))


class TalkPackTest(unittest.TestCase):
    def test_cloze_ignores_case(self):
        card = content.BY_ID["talk:my-bad"]
        question = srs.make_question(card, srs.CLOZE, random.Random(1))
        self.assertIn("<u>", question.prompt)
        self.assertNotIn("My bad", question.prompt)

    def test_custom_word_without_example_is_not_a_cloze(self):
        card = content.custom_card(1, "respawn", "возродиться")
        question = srs.make_question(card, srs.CLOZE, random.Random(1))
        self.assertEqual(question.kind, srs.RECALL)
        self.assertIn("respawn", question.options)

    def test_custom_word_is_escaped(self):
        card = content.custom_card(1, "a<b", "x & y")
        self.assertEqual((card.en, card.ru), ("a&lt;b", "x &amp; y"))


class MilestonesTest(unittest.TestCase):
    def test_new_level_is_celebrated(self):
        lines = milestones(49, 51, 2)
        self.assertTrue(any("+2" in line for line in lines))
        self.assertTrue(any("Новый уровень" in line for line in lines))

    def test_quiet_when_nothing_changed(self):
        self.assertEqual(milestones(10, 10, 2), [])
        self.assertEqual(milestones(None, 10, 2), [])

    def test_streak_marks(self):
        self.assertTrue(any("7 дней" in line for line in milestones(5, 5, 7)))


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

    async def test_own_word_goes_to_cards_today(self):
        card, created = await self.db.eng_add_word(USER_ID, "loot", "добыча", "", TODAY)
        self.assertTrue(created)
        self.assertTrue(content.is_custom(card.id))
        progress = await self.db.eng_progress_of(USER_ID, card.id)
        self.assertEqual((progress.box, progress.due_on), (0, TODAY))
        self.assertIn(card.id, srs.build_session(await self.db.eng_progress(USER_ID), TODAY))
        self.assertEqual((await self.db.eng_card(USER_ID, card.id)).ru, "добыча")

    async def test_same_word_updates_instead_of_duplicating(self):
        first, _ = await self.db.eng_add_word(USER_ID, "Loot", "добыча", "", TODAY)
        await self.db.eng_save_answer(USER_ID, first.id, 2, TODAY, True, False)
        second, created = await self.db.eng_add_word(USER_ID, "loot", "лут", "Grab the loot.", TODAY)

        self.assertFalse(created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(await self.db.eng_words(USER_ID)), 1)
        self.assertEqual((await self.db.eng_progress_of(USER_ID, first.id)).box, 2)
        self.assertEqual((await self.db.eng_find_word(USER_ID, "LOOT")).ru, "лут")

    async def test_own_words_are_private(self):
        card, _ = await self.db.eng_add_word(USER_ID, "loot", "добыча", "", TODAY)
        await self.db.ensure_user(USER_ID + 1)
        self.assertIsNone(await self.db.eng_card(USER_ID + 1, card.id))
        self.assertEqual(await self.db.eng_words(USER_ID + 1), [])

    async def test_deleting_own_word_drops_its_progress(self):
        card, _ = await self.db.eng_add_word(USER_ID, "loot", "добыча", "", TODAY)
        self.assertTrue(await self.db.eng_delete_word(USER_ID, card.id))
        self.assertIsNone(await self.db.eng_progress_of(USER_ID, card.id))
        self.assertFalse(await self.db.eng_delete_word(USER_ID, card.id))
        self.assertFalse(await self.db.eng_delete_word(USER_ID, "games:loot"))


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


class TypedAnswerTest(unittest.TestCase):
    def test_exact_ignores_case_and_punctuation(self):
        self.assertEqual(srs.check_typed("I'm down", "i’m down."), srs.EXACT)
        self.assertEqual(srs.check_typed("low-key", "low key"), srs.EXACT)

    def test_small_typo_counts(self):
        self.assertEqual(srs.check_typed("respawn", "respwn"), srs.TYPO)
        self.assertEqual(srs.check_typed("ghosted", "gosted"), srs.TYPO)

    def test_wrong_or_short(self):
        self.assertIsNone(srs.check_typed("bet", "bat"), "в коротком слове опечаток нет")
        self.assertIsNone(srs.check_typed("respawn", "reload"))
        self.assertIsNone(srs.check_typed("loot", ""))

    def test_type_question_for_familiar_words(self):
        progress = srs.Progress("games:loot", srs.TYPE_FROM_BOX, TODAY, 3, 3, 0)
        kinds = {srs.kind_for(progress, random.Random(seed)) for seed in range(40)}
        self.assertIn(srs.TYPE, kinds)
        fresh = srs.Progress("games:loot", 0, TODAY, 1, 0, 0)
        self.assertNotIn(
            srs.TYPE, {srs.kind_for(fresh, random.Random(seed)) for seed in range(40)}
        )

    def test_type_question_shape(self):
        card = content.BY_ID["talk:my-bad"]
        question = srs.make_question(card, srs.TYPE)
        self.assertEqual(question.options, (card.en,))
        self.assertIn(card.ru, question.prompt)
        self.assertNotIn("My bad", question.prompt, "ответ не подсказываем")


class PhraseTest(unittest.TestCase):
    def test_same_phrase_all_day(self):
        from bot.english import phrase

        self.assertEqual(phrase.pick(TODAY, []), phrase.pick(TODAY, []))
        self.assertIn(phrase.pick(TODAY, []).pack, phrase.PACKS)

    def test_prefers_what_is_not_studied(self):
        from bot.english import phrase

        pool = [card.id for card in content.CARDS if card.pack in phrase.PACKS]
        keep = pool[0]
        self.assertEqual(phrase.pick(TODAY, pool[1:]).id, keep)


class SpeakCompareTest(unittest.TestCase):
    def test_clean_read(self):
        from bot.english.speak import compare

        verdict = compare("My bad, I missed your call.", "my bad I missed your call")
        self.assertEqual(verdict.score, 1.0)
        self.assertNotIn("<u>", verdict.marked)

    def test_missed_words_are_marked(self):
        from bot.english.speak import compare

        verdict = compare("Hang on, I'll be right back.", "hang on I be right back")
        self.assertLess(verdict.score, 1.0)
        self.assertIn("<u>I'll</u>", verdict.marked)
        self.assertIn("Hang", verdict.marked)


class QuestLinesTest(unittest.TestCase):
    def test_line_with_word_has_no_markup(self):
        line = quests.line_with(quests.BY_ID["tavern"], "rumor")
        self.assertIn("rumors", line)
        self.assertNotIn("<b>", line)
        self.assertEqual(quests.line_with(quests.BY_ID["tavern"], "zzz"), "")


class PhraseStorageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = memory_db()
        await self.db.connect()
        await self.db.ensure_user(USER_ID)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_phrase_stays_after_adding_it(self):
        from bot.english import phrase

        card, first = await phrase.phrase_of_day(self.db, USER_ID, TODAY)
        self.assertTrue(first)
        await self.db.eng_save_answer(USER_ID, card.id, 0, TODAY, False, False)
        again, first = await phrase.phrase_of_day(self.db, USER_ID, TODAY)
        self.assertEqual((again.id, first), (card.id, False))

    async def test_reminder_carries_phrase_once_a_day(self):
        from bot.english import phrase

        text, markup = await phrase.reminder_text(self.db, USER_ID, TODAY, "base")
        self.assertIn("фраза дня", text)
        self.assertIsNotNone(markup)
        text, markup = await phrase.reminder_text(self.db, USER_ID, TODAY, "base")
        self.assertEqual((text, markup), ("base", None))
        tomorrow, _ = await phrase.reminder_text(
            self.db, USER_ID, TODAY + dt.timedelta(days=1), "base"
        )
        self.assertIn("фраза дня", tomorrow)


class ProgressPictureTest(unittest.TestCase):
    def test_colors_grow_with_answers(self):
        from bot.english import charts

        self.assertEqual(charts.color_for(0), charts.COLOR_EMPTY)
        shades = [charts.color_for(count) for count in (1, 10, 20, 40, 400)]
        self.assertEqual(shades[:4], [step for _, step in charts.STEPS])
        self.assertEqual(shades[4], charts.STEPS[-1][1])

    def test_calendar_ends_with_this_week(self):
        from bot.english import charts

        start = charts.calendar_start(TODAY)
        self.assertEqual(start.weekday(), 0)
        self.assertEqual((TODAY - start).days // 7, charts.WEEKS - 1)

    def test_next_level(self):
        from bot.english.handlers.progress import LEVELS, next_level

        self.assertEqual(next_level(0), (0, LEVELS[1]))
        self.assertEqual(next_level(60)[0], 50)
        self.assertIsNone(next_level(10_000)[1])

    def test_picture_renders(self):
        from bot.english import charts

        if not charts.available():
            self.skipTest("нет matplotlib")
        days = {TODAY - dt.timedelta(days=offset): 10 for offset in range(0, 40, 2)}
        for upcoming in ((90, "A2 — держу простой диалог"), None):
            image = charts.progress_png(
                charts.Snapshot(
                    today=TODAY, days=days, learned=63, in_work=90, streak=1,
                    level="A1", next_level=upcoming, level_floor=50,
                    packs=[("Основа", 3, 40), ("Мои слова", 0, 2)],
                )
            )
            self.assertTrue(image.startswith(b"\x89PNG"))


class SessionLookTest(unittest.TestCase):
    def test_progress_bar(self):
        from bot.english.handlers.session import progress_bar

        self.assertEqual(progress_bar(0, 10), "▱" * 10)
        self.assertEqual(progress_bar(5, 10), "▰" * 5 + "▱" * 5)
        self.assertEqual(progress_bar(3, 0), "▱" * 10)

    def test_verdict(self):
        from bot.english.handlers.session import verdict

        self.assertIn("отлично", verdict(10, 10))
        self.assertIn("вернутся", verdict(1, 10))


class TidyOnceTest(unittest.IsolatedAsyncioTestCase):
    """Старый дневник: давление и деньги уходят с глаз, английский — вперёд."""

    async def test_existing_diary_is_tidied_once(self):
        from bot import sections

        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "diary.db")
            db = Database(path, "Europe/Moscow")
            await db.connect()
            await db.ensure_user(USER_ID)
            await db.add_measurement(USER_ID, 120, 80, 68, dt.datetime(2026, 8, 1, 8, 0))
            fresh_hidden = await db.get_meta(sections.hidden_key(USER_ID))
            await db.close()
            self.assertFalse(fresh_hidden, "новая база начинается со всеми разделами")

            db = Database(path, "Europe/Moscow")
            await db.connect()
            try:
                hidden = set((await db.get_meta(sections.hidden_key(USER_ID))).split(","))
                self.assertEqual(hidden, {"bp", "money", "health"})
                for topic in (sections.PRESSURE, sections.MONEY, sections.HEALTH):
                    reminders = await db.list_reminders(USER_ID, topic)
                    self.assertTrue(reminders and not any(r.enabled for r in reminders))
                english = await db.list_reminders(USER_ID, sections.ENGLISH)
                self.assertTrue(all(r.enabled for r in english))
                self.assertEqual((await db.ensure_user(USER_ID)).section, sections.ENGLISH)
                self.assertTrue(await db.last_measurements(USER_ID), "записи на месте")

                # вернул раздел сам — повторный запуск его не прячет
                await db.set_meta(sections.hidden_key(USER_ID), "money")
            finally:
                await db.close()

            db = Database(path, "Europe/Moscow")
            await db.connect()
            try:
                self.assertEqual(await db.get_meta(sections.hidden_key(USER_ID)), "money")
            finally:
                await db.close()
