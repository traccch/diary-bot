"""Оформление консоли: шапка, строки лога, журнал событий."""

from __future__ import annotations

import datetime as dt
import logging
import tempfile
import unittest
from pathlib import Path

from aiogram.types import Chat, Message, User, Voice

from bot import console, journal
from bot.console import Palette, Startup, banner, box, cell_width, human_size, visible

NOW = dt.datetime(2026, 8, 27, 23, 15)


def sample() -> Startup:
    return Startup(
        username="mihailovich_diary_bot",
        branch="main",
        commit="603d6ad",
        tz="Europe/Moscow",
        now=NOW,
        db_path="data/diary.db",
        db_bytes=1_260_000,
        counts=["давление 34", "траты 12", "слова 6/133"],
        reminders=7,
        next_reminder="08:00 (через 8 ч 45 мин)",
        features=[("Графики", True, "matplotlib на месте"), ("Голос", False, "выключен")],
    )


class WidthTest(unittest.TestCase):
    def test_ansi_does_not_count(self):
        painted = Palette(True).paint("привет", "green")
        self.assertNotEqual(painted, "привет")
        self.assertEqual(visible(painted), "привет")

    def test_wide_characters_take_two_cells(self):
        self.assertEqual(cell_width("абв"), 3)
        self.assertEqual(cell_width("⏰"), 2)

    def test_human_size(self):
        self.assertEqual(human_size(512), "512 Б")
        self.assertEqual(human_size(20_000), "20 КБ")
        self.assertEqual(human_size(1_260_000), "1,2 МБ")


class BannerTest(unittest.TestCase):
    def widths(self, text: str) -> set[int]:
        return {cell_width(visible(line)) for line in text.splitlines() if "│" in line or "┌" in line}

    def test_frame_is_rectangular(self):
        self.assertEqual(self.widths(banner(sample())), {console.WIDTH})

    def test_frame_is_rectangular_in_color_too(self):
        """Цвет не должен разъезжаться рамкой: ширина считается по видимым знакам."""
        self.assertEqual(self.widths(banner(sample(), Palette(True))), {console.WIDTH})

    def test_shows_what_matters(self):
        text = banner(sample())
        for fragment in (
            "https://t.me/mihailovich_diary_bot",
            "main · 603d6ad",
            "Europe/Moscow · сейчас 23:15",
            "1,2 МБ",
            "давление 34 · траты 12",
            "ближайшее 08:00 (через 8 ч 45 мин)",
            "Ctrl+C",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_reminders_off_is_visible(self):
        info = sample()
        info.reminders = 0
        self.assertIn("выключены", banner(info))

    def test_long_value_does_not_break_the_frame(self):
        info = sample()
        info.features = [("Голос", False, "не нашёл whisper.cpp по пути " + "C:/" + "x" * 60)]
        self.assertEqual(self.widths(banner(info)), {console.WIDTH})
        self.assertIn("…", banner(info))

    def test_clip_keeps_colors_intact(self):
        painted = Palette(True).paint("очень длинное значение", "green")
        clipped = console.clip(painted, 10)
        self.assertEqual(cell_width(visible(clipped)), 10)
        self.assertTrue(clipped.endswith("\033[0m"))


class FormatterTest(unittest.TestCase):
    def line(self, level=logging.INFO, name="bot.main", message="Бот запущен", **extra):
        record = logging.LogRecord(name, level, __file__, 1, message, (), None)
        for key, value in extra.items():
            setattr(record, key, value)
        return console.PrettyFormatter(Palette(False)).format(record)

    def test_time_and_message(self):
        text = self.line()
        self.assertRegex(text, r"^\d\d:\d\d:\d\d · Бот запущен$")

    def test_warning_is_marked(self):
        self.assertIn("!", self.line(logging.WARNING, message="Связь рвётся"))

    def test_error_is_marked(self):
        self.assertIn("✗", self.line(logging.ERROR, message="Всё сломалось"))

    def test_foreign_logger_is_named(self):
        self.assertIn("[aiogram]", self.line(name="aiogram.dispatcher"))

    def test_own_logger_is_not_named(self):
        self.assertNotIn("[bot]", self.line(name="bot.reminders"))

    def test_color_is_optional(self):
        record = logging.LogRecord("bot.main", logging.ERROR, __file__, 1, "Ой", (), None)
        self.assertNotIn("\033", console.PrettyFormatter(Palette(False)).format(record))
        self.assertIn("\033", console.PrettyFormatter(Palette(True)).format(record))


def message(text=None, **fields) -> Message:
    return Message(
        message_id=1,
        date=dt.datetime.now(dt.timezone.utc),
        chat=Chat(id=1, type="private"),
        from_user=User(id=2, is_bot=False, first_name="Михаил"),
        text=text,
        **fields,
    )


class JournalTest(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(journal.describe(message("кофе 300")), "«кофе 300»")
        self.assertEqual(journal.describe(message("/stats")), "/stats")
        self.assertEqual(
            journal.describe(
                message(voice=Voice(file_id="v", file_unique_id="u", duration=5))
            ),
            "голосовое, 5 с",
        )

    def test_long_text_is_cut(self):
        described = journal.describe(message("слово " * 40))
        self.assertLess(len(described), 70)
        self.assertIn("…", described)

    def test_newlines_are_flattened(self):
        self.assertEqual(journal.describe(message("первая\nвторая")), "«первая вторая»")

    def test_took(self):
        self.assertEqual(journal.took(2.855), "2,9 с")
        self.assertEqual(journal.took(0.31), "310 мс")

    def test_who(self):
        self.assertEqual(journal.who(message("привет")), "Михаил")


class JournalMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    async def test_writes_one_line_per_event(self):
        async def handler(event, data):
            return "ok"

        with self.assertLogs("bot.journal", level="INFO") as logged:
            result = await journal.JournalMiddleware()(handler, message("кофе 300"), {})

        self.assertEqual(result, "ok")
        self.assertIn("Михаил → «кофе 300»", logged.output[0])

    async def test_failure_is_logged_and_reraised(self):
        async def handler(event, data):
            raise RuntimeError("сломалось")

        with self.assertLogs("bot.journal", level="ERROR") as logged:
            with self.assertRaises(RuntimeError):
                await journal.JournalMiddleware()(handler, message("/stats"), {})

        self.assertIn("сорвалось", logged.output[0])


class HeartbeatTest(unittest.IsolatedAsyncioTestCase):
    """Часовая отметка: в тихом окне должно быть видно, что бот жив."""

    async def asyncSetUp(self):
        from bot.db import Database

        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "diary.db"), "Europe/Moscow")
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    def make(self, counter):
        from bot.heartbeat import Heartbeat

        self.clock = iter([0.0, 3600.0, 7200.0, 10800.0])
        return Heartbeat(self.db, counter, clock=lambda: next(self.clock))

    async def test_quiet_hour(self):
        beat = self.make(journal.Counter())
        line = await beat.line()
        self.assertIn("на месте", line)
        self.assertIn("за час тихо", line)

    async def test_counts_events_and_resets(self):
        counter = journal.Counter()
        for _ in range(3):
            counter.add()
        beat = self.make(counter)

        self.assertIn("за час 3 события", await beat.line())
        self.assertIn("за час тихо", await beat.line())  # счёт обнулился
        self.assertEqual(counter.total, 3)  # всего — не обнуляется

    async def test_mentions_next_reminder(self):
        await self.db.ensure_user(777)
        line = await self.make(journal.Counter()).line()
        self.assertRegex(line, r"дальше \d\d:\d\d \(")

    async def test_uptime_wording(self):
        from bot.heartbeat import uptime_text

        self.assertEqual(uptime_text(50 * 60), "50 мин")
        self.assertEqual(uptime_text(75 * 60), "1 ч 15 мин")
        self.assertEqual(uptime_text(3 * 86400 + 7200), "3 дня 2 ч")


class StartupTest(unittest.IsolatedAsyncioTestCase):
    """Шапка собирается из живой базы и не падает, когда чего-то нет."""

    async def asyncSetUp(self):
        from bot.db import Database

        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "diary.db")
        self.db = Database(self.path, "Europe/Moscow")
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def collect(self):
        from bot.config import Config, VoiceConfig
        from bot.main import collect_startup
        from bot.updater import Updater
        from bot.voice import build_transcriber

        config = Config(
            token="42:TEST",
            db_path=self.path,
            default_tz="Europe/Moscow",
            log_level="INFO",
            owner_id=None,
            auto_update_check=False,
            polling_timeout=15,
            voice=VoiceConfig(),
        )

        class NoGit(Updater):
            def is_git_repo(self):
                return False

        return await collect_startup(
            "diary_bot", self.db, config, NoGit(), build_transcriber(VoiceConfig())
        )

    async def test_empty_database(self):
        info = await self.collect()
        self.assertIn("пока пусто", " ".join(info.counts))
        self.assertEqual(info.reminders, 0)
        self.assertIn("Голос", banner(info))

    async def test_with_owner_and_records(self):
        await self.db.ensure_user(777)  # заодно поставит напоминания по умолчанию
        await self.db.add_measurement(777, 120, 80, 65, dt.datetime(2026, 8, 27, 8, 0))

        info = await self.collect()
        self.assertIn("давление 1", info.counts)
        self.assertGreater(info.reminders, 0)
        self.assertRegex(info.next_reminder, r"^\d\d:\d\d \(")
        self.assertIn("diary.db", banner(info))


if __name__ == "__main__":
    unittest.main()
