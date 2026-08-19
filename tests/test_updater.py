"""Самообновление: проверка новых коммитов, откат при красных тестах, уведомления.

Тесты работают с настоящим git на временных репозиториях: именно в общении
с git и живёт риск, ради которого всё это и проверяется.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from bot.db import Database
from bot.handlers.update import is_owner, render_status
from bot.updater import UpdateError, UpdateWatcher, Updater

GIT = shutil.which("git")

PASSING_TEST = """import unittest


class Ok(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
"""

FAILING_TEST = PASSING_TEST.replace("self.assertTrue(True)", "self.assertTrue(False)")


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        [
            GIT,
            "-c", "user.email=test@example.com",
            "-c", "user.name=Тест",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@unittest.skipUnless(GIT, "git не установлен")
class UpdaterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)

        self.origin = root / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "--initial-branch=main")
        self.write_project(self.origin, PASSING_TEST)
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-m", "Первая версия")

        self.clone = root / "clone"
        git(root, "clone", "--quiet", str(self.origin), str(self.clone))

        self.updater = Updater(root=self.clone)

    async def asyncTearDown(self):
        self._tmp.cleanup()

    def write_project(self, where: Path, test_body: str) -> None:
        (where / "requirements.txt").write_text("", encoding="utf-8")
        tests = where / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (tests / "test_sample.py").write_text(test_body, encoding="utf-8")

    def commit_upstream(self, message: str, test_body: str = PASSING_TEST) -> None:
        self.write_project(self.origin, test_body)
        (self.origin / "bot.py").write_text(f"# {message}\n", encoding="utf-8")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "-m", message)

    # ------------------------------------------------------------- проверка

    async def test_up_to_date(self):
        status = await self.updater.check()
        self.assertFalse(status.available)
        self.assertEqual(status.behind, 0)
        self.assertIn("Последняя версия", render_status(status))

    async def test_sees_new_commits(self):
        self.commit_upstream("Починил разбор сна")
        self.commit_upstream("Добавил график веса")

        status = await self.updater.check()
        self.assertTrue(status.available)
        self.assertEqual(status.behind, 2)
        self.assertEqual(
            status.messages, ["Добавил график веса", "Починил разбор сна"]
        )
        self.assertIn("Есть обновление", render_status(status))
        self.assertIn("Добавил график веса", render_status(status))

    async def test_renamed_branch_is_explained(self):
        """Ветку переименовали на сервере — git ругается невнятно, бот объясняет."""
        git(self.clone, "branch", "-m", "старое-имя")
        git(self.clone, "branch", "--set-upstream-to=origin/main", "старое-имя")
        with self.assertRaises(UpdateError) as caught:
            await self.updater.check()
        self.assertIn("переименовали", str(caught.exception))

    async def test_not_a_repo(self):
        plain = Updater(root=Path(self._tmp.name) / "origin" / "tests")
        self.assertFalse(plain.is_git_repo())
        result = await plain.apply()
        self.assertFalse(result.ok)
        self.assertIn("не git-репозиторий", result.message)

    # ------------------------------------------------------------ обновление

    async def test_applies_and_asks_for_restart(self):
        self.commit_upstream("Новая версия")
        before = await self.updater.commit()

        result = await self.updater.apply(run_tests=False)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.restart)
        self.assertNotEqual(await self.updater.commit(), before)
        self.assertTrue((self.clone / "bot.py").exists())

    async def test_nothing_to_apply(self):
        result = await self.updater.apply(run_tests=False)
        self.assertTrue(result.ok)
        self.assertFalse(result.restart)
        self.assertIn("нечего", result.message)

    async def test_local_changes_are_not_wiped(self):
        (self.clone / "bot.py").write_text("моя правка\n", encoding="utf-8")
        git(self.clone, "add", "-A")
        self.commit_upstream("Обновление сверху")

        result = await self.updater.apply(run_tests=False)
        self.assertFalse(result.ok)
        self.assertIn("несохранённые правки", result.message)
        self.assertEqual(
            (self.clone / "bot.py").read_text(encoding="utf-8"), "моя правка\n"
        )

    async def test_green_tests_keep_the_update(self):
        self.commit_upstream("Рабочая версия")
        result = await self.updater.apply(run_tests=True)
        self.assertTrue(result.ok, result.message)
        self.assertTrue((self.clone / "bot.py").exists())

    async def test_broken_update_is_rolled_back(self):
        before = await self.updater.commit()
        self.commit_upstream("Сломанная версия", test_body=FAILING_TEST)

        result = await self.updater.apply(run_tests=True)
        self.assertFalse(result.ok)
        self.assertFalse(result.restart)
        self.assertIn("не прошла тесты", result.message)
        # вернулись ровно туда, где были
        self.assertEqual(await self.updater.commit(), before)
        self.assertFalse((self.clone / "bot.py").exists())


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        self.sent.append((chat_id, text))


class FakeUpdater:
    def __init__(self, status) -> None:
        self._status = status

    def is_git_repo(self) -> bool:
        return True

    async def check(self):
        return self._status


class OwnerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "t.db"), "Europe/Moscow")
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def test_owner_from_config_wins(self):
        await self.db.ensure_user(111)
        self.assertTrue(await is_owner(self.db, 999, 999))
        self.assertFalse(await is_owner(self.db, 999, 111))

    async def test_first_user_becomes_owner(self):
        await self.db.ensure_user(111)
        await self.db.ensure_user(222)
        self.assertEqual(await self.db.owner_id(), 111)
        self.assertTrue(await is_owner(self.db, None, 111))
        self.assertFalse(await is_owner(self.db, None, 222))

    async def test_empty_diary_has_no_owner_yet(self):
        self.assertIsNone(await self.db.owner_id())
        self.assertTrue(await is_owner(self.db, None, 555))


class WatcherTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "t.db"), "Europe/Moscow")
        await self.db.connect()
        await self.db.ensure_user(777)
        self.bot = FakeBot()

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    def watcher(self, behind: int, remote: str = "abc1234"):
        from bot.updater import UpdateStatus

        status = UpdateStatus(
            branch="main", local="0000000", remote=remote, behind=behind,
            messages=["Починил разбор сна"],
        )
        return UpdateWatcher(self.bot, self.db, FakeUpdater(status))

    async def test_notifies_once_per_commit(self):
        watcher = self.watcher(behind=1)
        self.assertTrue(await watcher.check_once())
        self.assertIn("Вышло обновление", self.bot.sent[0][1])
        self.assertEqual(self.bot.sent[0][0], 777)

        # про тот же коммит второй раз не пишем
        self.assertFalse(await watcher.check_once())
        self.assertEqual(len(self.bot.sent), 1)

        # а про следующий — пишем
        self.assertTrue(await self.watcher(behind=2, remote="def5678").check_once())
        self.assertEqual(len(self.bot.sent), 2)

    async def test_silent_when_up_to_date(self):
        self.assertFalse(await self.watcher(behind=0).check_once())
        self.assertEqual(self.bot.sent, [])

    async def test_silent_without_owner(self):
        empty = Database(str(Path(self._tmp.name) / "empty.db"), "Europe/Moscow")
        await empty.connect()
        watcher = UpdateWatcher(self.bot, empty, FakeUpdater(None))
        self.assertFalse(await watcher.check_once())
        await empty.close()


class RestartFlagTest(unittest.IsolatedAsyncioTestCase):
    async def test_event_stops_polling(self):
        """Событие перезапуска должно выигрывать гонку у бесконечного polling."""
        from bot.main import _poll_until_stopped
        from bot.updater import RESTART_CODE

        class FakeDispatcher:
            def __init__(self, event):
                self.stopped = False
                self._event = event

            async def start_polling(self, bot):
                while not self.stopped:
                    await asyncio.sleep(0.01)

            async def stop_polling(self):
                self.stopped = True

        event = asyncio.Event()
        dispatcher = FakeDispatcher(event)

        async def request_restart():
            await asyncio.sleep(0.05)
            event.set()

        asyncio.create_task(request_restart())
        code = await _poll_until_stopped(dispatcher, object(), event)

        self.assertEqual(code, RESTART_CODE)
        self.assertTrue(dispatcher.stopped)


if __name__ == "__main__":
    unittest.main()
