"""Самообновление: бот забирает новый код из GitHub по команде владельца.

Ход обновления: git pull → доустановка зависимостей, если менялся
requirements.txt → прогон тестов → перезапуск. Если тесты после обновления
красные, код откатывается на прежний коммит: лучше остаться на старой,
но рабочей версии, чем упасть в тот момент, когда хозяин не у компьютера.

Сам процесс себя не перезапускает — он выходит с кодом RESTART_CODE,
а поднимает его заново скрипт запуска (run.sh / run.bat). Так надёжнее:
если новая версия не стартует, видно, на чём именно всё встало.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

#: С таким кодом процесс просит скрипт запуска поднять его заново.
RESTART_CODE = 42

#: Шаги обновления, о которых сообщаем наружу. Обновление идёт минуту-другую,
#: и всё это время бот молчал — со стороны неотличимо от зависшего.
PULL = "pull"
DEPS = "deps"
TESTS = "tests"
RESTART = "restart"

#: Куда сообщать о ходе дела. None — молча, как раньше.
Progress = Optional[Callable[[str], Awaitable[None]]]

GIT_TIMEOUT = 120
PIP_TIMEOUT = 900  # matplotlib и numpy ставятся долго
TESTS_TIMEOUT = 600


@dataclass(frozen=True)
class UpdateStatus:
    """Что нового в удалённом репозитории."""

    branch: str
    local: str
    remote: str
    behind: int
    messages: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.behind > 0


@dataclass(frozen=True)
class UpdateResult:
    ok: bool
    message: str
    restart: bool = False


class UpdateError(RuntimeError):
    """Обновиться не вышло, причина — в тексте."""


async def run_command(
    args: Sequence[str], cwd: Path, timeout: int
) -> tuple[int, str]:
    """Запускает команду и возвращает (код возврата, вывод)."""
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise UpdateError(f"Команда {' '.join(args[:2])} не уложилась в {timeout} с")
    return process.returncode, stdout.decode("utf-8", "replace").strip()


async def _step(progress: Progress, step: str) -> None:
    """Сообщает о шаге, но не даёт отчёту сорвать обновление."""
    if progress is None:
        return
    try:
        await progress(step)
    except Exception:  # noqa: BLE001 - сообщение о ходе дела важнее самого дела не бывает
        logger.debug("Не смог сообщить о шаге %s", step, exc_info=True)


class Updater:
    def __init__(self, root: Optional[Path] = None, python: Optional[str] = None) -> None:
        self.root = root or Path(__file__).resolve().parent.parent
        self.python = python or sys.executable

    # ------------------------------------------------------------- состояние

    def is_git_repo(self) -> bool:
        return (self.root / ".git").exists()

    async def _git(self, *args: str, timeout: int = GIT_TIMEOUT) -> str:
        code, output = await run_command(["git", *args], self.root, timeout)
        if code != 0:
            raise UpdateError(output or f"git {' '.join(args)} завершился с кодом {code}")
        return output

    async def branch(self) -> str:
        return await self._git("rev-parse", "--abbrev-ref", "HEAD")

    async def commit(self, ref: str = "HEAD") -> str:
        return await self._git("rev-parse", "--short", ref)

    async def is_dirty(self) -> bool:
        """Есть ли незакоммиченные правки — тогда обновляться опасно."""
        return bool(await self._git("status", "--porcelain"))

    # ------------------------------------------------------------- проверка

    async def check(self) -> UpdateStatus:
        if not self.is_git_repo():
            raise UpdateError(
                "Бот запущен не из git-репозитория, обновляться неоткуда. "
                "Помогает переустановка через git clone."
            )
        branch = await self.branch()
        try:
            await self._git("fetch", "--quiet", "origin", branch)
        except UpdateError as exc:
            # ветку могли переименовать на GitHub — git скажет об этом невнятно
            if "couldn't find remote ref" in str(exc).lower():
                raise UpdateError(
                    f"В репозитории больше нет ветки {branch} — похоже, её переименовали. "
                    "Помогает `git checkout main` в папке бота или свежий git clone."
                ) from exc
            raise

        upstream = f"origin/{branch}"
        behind_raw = await self._git("rev-list", "--count", f"HEAD..{upstream}")
        messages = await self._git("log", "--format=%s", f"HEAD..{upstream}")

        return UpdateStatus(
            branch=branch,
            local=await self.commit(),
            remote=await self.commit(upstream),
            behind=int(behind_raw or 0),
            messages=[line for line in messages.splitlines() if line.strip()],
        )

    # ------------------------------------------------------------ обновление

    async def _requirements_changed(self, before: str) -> bool:
        changed = await self._git("diff", "--name-only", before, "HEAD")
        return "requirements.txt" in changed.splitlines()

    async def _install_requirements(self) -> None:
        code, output = await run_command(
            [self.python, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"],
            self.root,
            PIP_TIMEOUT,
        )
        if code != 0:
            raise UpdateError(f"Не удалось поставить зависимости:\n{output[-500:]}")

    async def _run_tests(self) -> tuple[bool, str]:
        code, output = await run_command(
            [self.python, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
            self.root,
            TESTS_TIMEOUT,
        )
        tail = output.strip().splitlines()
        return code == 0, "\n".join(tail[-6:])

    async def apply(
        self, run_tests: bool = True, progress: Progress = None
    ) -> UpdateResult:
        """Забирает новую версию, проверяет её и просит перезапуск."""
        if not self.is_git_repo():
            return UpdateResult(False, "Обновляться неоткуда: это не git-репозиторий.")

        if await self.is_dirty():
            return UpdateResult(
                False,
                "В папке бота есть несохранённые правки — не буду их затирать. "
                "Разберись с ними у компьютера и попробуй снова.",
            )

        status = await self.check()
        if not status.available:
            return UpdateResult(True, "И так последняя версия, обновлять нечего.")

        before = await self.commit()
        await _step(progress, PULL)
        try:
            await self._git("merge", "--ff-only", f"origin/{status.branch}")
        except UpdateError as exc:
            return UpdateResult(False, f"Не удалось обновиться: {exc}")

        try:
            if await self._requirements_changed(before):
                await _step(progress, DEPS)
                await self._install_requirements()
        except UpdateError as exc:
            await self._rollback(before)
            return UpdateResult(False, f"{exc}\nВернул прежнюю версию.")

        if run_tests:
            await _step(progress, TESTS)
            passed, tail = await self._run_tests()
            if not passed:
                await self._rollback(before)
                return UpdateResult(
                    False,
                    "Новая версия не прошла тесты, поэтому я вернул прежнюю — "
                    f"бот работает как работал.\n\n<code>{tail}</code>",
                )

        await _step(progress, RESTART)
        after = await self.commit()
        return UpdateResult(
            True,
            f"Обновился: <code>{before}</code> → <code>{after}</code> "
            f"({status.behind} "
            + ("коммит" if status.behind == 1 else "коммитов")
            + "). Перезапускаюсь.",
            restart=True,
        )

    async def _rollback(self, commit: str) -> None:
        try:
            await self._git("reset", "--hard", commit)
        except UpdateError:
            logger.exception("Не смог откатиться на %s", commit)


class UpdateWatcher:
    """Раз в несколько часов смотрит, не появилось ли новой версии.

    Сам ничего не применяет: молча тянуть код на машину, к которой хозяин не
    подходил, — плохая идея. Приходит сообщение с кнопкой, решение за человеком.
    Про один и тот же коммит напоминаем ровно раз.
    """

    def __init__(
        self,
        bot,
        db,
        updater: "Updater",
        owner_id: Optional[int] = None,
        interval_hours: int = 6,
    ) -> None:
        self._bot = bot
        self._db = db
        self._updater = updater
        self._owner_id = owner_id
        self._interval = interval_hours * 3600
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="update-watcher")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - проверка обновлений не должна ронять бота
                logger.exception("Не смог проверить обновления")
            await asyncio.sleep(self._interval)

    async def check_once(self) -> bool:
        """Возвращает True, если владельцу ушло сообщение о новой версии."""
        if not self._updater.is_git_repo():
            return False

        owner = self._owner_id or await self._db.owner_id()
        if owner is None:
            return False  # боту ещё никто не писал — некому и сообщать

        status = await self._updater.check()
        if not status.available:
            return False
        if await self._db.get_meta("notified_commit") == status.remote:
            return False

        from .keyboards import update_actions  # локально: иначе кольцо импортов

        lines = [
            f"🆕 <b>Вышло обновление бота</b>: {status.behind} "
            + ("коммит" if status.behind == 1 else "коммитов"),
            "",
        ]
        lines.extend(f"· {message}" for message in status.messages[:5])
        if len(status.messages) > 5:
            lines.append(f"<i>…и ещё {len(status.messages) - 5}</i>")
        lines.append("")
        lines.append("<i>Обновление проверится тестами, и если что-то не так — откачу.</i>")

        await self._bot.send_message(
            owner, "\n".join(lines), reply_markup=update_actions()
        )
        await self._db.set_meta("notified_commit", status.remote)
        return True
