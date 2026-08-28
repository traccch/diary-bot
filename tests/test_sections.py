"""Разделы: переключение, диспетчер команд и умный разбор свободного текста."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest

from bot import sections
from bot.money.handlers.entry import NOT_FOUND as MONEY_NOT_FOUND
from bot.money.handlers.entry import save_transaction
from bot.pressure.parsing import looks_like_pressure
from bot.voice import VoiceConfig, build_transcriber, clean_speech

from .support import memory_db

TODAY = dt.date(2026, 8, 17)


class SectionsTest(unittest.TestCase):
    def test_known_sections(self):
        self.assertEqual(
            {s.key for s in sections.SECTIONS}, {"pressure", "money", "english"}
        )
        self.assertEqual(sections.section_of("money").title, "Деньги")

    def test_unknown_falls_back_to_default(self):
        self.assertEqual(sections.section_of("нет такого").key, sections.DEFAULT)


class LooksLikePressureTest(unittest.TestCase):
    def test_unambiguous(self):
        for text in ("120/80", "120/80 68", "120 на 80", "135\\85", "120 80 68", "120 80"):
            with self.subTest(text=text):
                self.assertTrue(looks_like_pressure(text))

    def test_money_is_left_alone(self):
        for text in ("кофе 300", "450 такси", "+90000 зарплата", "12080", "аренда 45к",
                     "продукты 1 250,50", ""):
            with self.subTest(text=text):
                self.assertFalse(looks_like_pressure(text))

    def test_pressure_with_a_note_is_not_forced(self):
        """С комментарием строку разбирает текущий раздел — «кофе 120 80» не давление."""
        self.assertFalse(looks_like_pressure("кофе 120 80"))


class FakeMessage:
    """Достаточно для save_transaction: он только отвечает текстом."""

    def __init__(self) -> None:
        self.answers: list[str] = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append(text)


class CrossSectionTest(unittest.IsolatedAsyncioTestCase):
    """Голое число не должно уезжать в расходы из раздела давления."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = memory_db()
        await self.db.connect()
        self.user = await self.db.ensure_user(777)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def save(self, text: str, crossing: bool) -> str:
        return await save_transaction(
            FakeMessage(), text, self.db, self.user, TODAY, crossing
        )

    async def test_bare_number_does_not_cross(self):
        self.assertEqual(await self.save("12080", crossing=True), MONEY_NOT_FOUND)
        self.assertEqual(await self.db.count_transactions(777), 0)

    async def test_bare_number_works_inside_the_section(self):
        self.assertNotEqual(await self.save("12080", crossing=False), MONEY_NOT_FOUND)
        self.assertEqual(await self.db.count_transactions(777), 1)

    async def test_number_with_a_word_crosses(self):
        self.assertNotEqual(await self.save("кофе 300", crossing=True), MONEY_NOT_FOUND)
        self.assertEqual(await self.db.count_transactions(777), 1)

    async def test_explicit_sign_crosses(self):
        self.assertNotEqual(await self.save("+5000", crossing=True), MONEY_NOT_FOUND)
        self.assertEqual(await self.db.count_transactions(777), 1)

    async def test_broken_amount_stays_quiet_when_crossing(self):
        message = FakeMessage()
        result = await save_transaction(
            message, "кофе 0", self.db, self.user, TODAY, require_context=True
        )
        self.assertEqual(result, MONEY_NOT_FOUND)
        self.assertEqual(message.answers, [])  # чужой раздел не ругается


class VoiceTest(unittest.TestCase):
    def test_disabled_by_default(self):
        transcriber = build_transcriber(VoiceConfig())
        self.assertFalse(transcriber.ready)
        self.assertIn("не настроено", transcriber.why_not_ready())

    def test_missing_binary_is_explained(self):
        transcriber = build_transcriber(
            VoiceConfig(binary="/нет/такого/whisper", model="/нет/такой/модели")
        )
        self.assertFalse(transcriber.ready)
        self.assertIn("не нашёл", transcriber.why_not_ready())

    def test_clean_speech(self):
        self.assertEqual(clean_speech("  120 на 80, пульс 68.  "), "120 на 80, пульс 68")
        self.assertEqual(clean_speech(""), "")


if __name__ == "__main__":
    unittest.main()
