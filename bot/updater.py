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
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence

from .formatting import plural

logger = logging.getLogger(__name__)

#: С таким кодом процесс просит скрипт запуска поднять его заново.
RESTART_CODE = 42

#: Шаги обновления, о которых сообщаем наружу. Обновление идёт минуту-другую,
#: и всё это время бот молчал — со стороны неотличимо от зависшего.
PULL = "pull"
DEPS = "deps"
TESTS = "tests"
RESTART = "restart"

#: Порядок шагов и их названия — одни и те же в чате и в консоли.
STEP_ORDER: tuple[str, ...] = (PULL, DEPS, TESTS, RESTART)
STEP_TITLES: dict[str, str] = {
    PULL: "Забираю новый код",
    DEPS: "Ставлю зависимости",
    TESTS: "Прогоняю тесты",
    RESTART: "Перезапускаюсь",
}

#: Куда сообщать о ходе дела. None — молча, как раньше.
Progress = Optional[Callable[[str], Awaitable[None]]]

#: Больше процессов не помогает: тесты упираются в запуск самого питона.
MAX_TEST_WORKERS = 4

_CLASS_LINE = re.compile(r"^class (\w+)\(")
_TEST_LINE = re.compile(r"^    (?:async )?def test_")

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
    #: Человеческие имена версий: «v1.2» вместо «317436e». Пусто — тегов нет.
    local_name: str = ""
    remote_name: str = ""

    @property
    def available(self) -> bool:
        return self.behind > 0

    @property
    def here(self) -> str:
        return self.local_name or self.local

    @property
    def there(self) -> str:
        return self.remote_name or self.remote


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
        env={
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            # Без этого на Windows дочерний python пишет в трубу в кодировке
            # консоли, и русский текст ошибок приезжает мусором.
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise UpdateError(f"Команда {' '.join(args[:2])} не уложилась в {timeout} с")
    return process.returncode, stdout.decode("utf-8", "replace").strip()


async def _step(progress: Progress, step: str) -> None:
    """Сообщает о шаге — в консоль всегда, в чат если есть кому."""
    logger.info("Обновление · %s", STEP_TITLES.get(step, step))
    if progress is None:
        return
    try:
        await progress(step)
    except Exception:  # noqa: BLE001 - сообщение о ходе дела важнее самого дела не бывает
        logger.debug("Не смог сообщить о шаге %s", step, exc_info=True)


#: Файл с именем версии в корне репозитория. Тег был бы уместнее, но файл
#: приезжает вместе с кодом обычным git pull и виден в любом клоне, даже когда
#: теги не выгружены.
VERSION_FILE = "VERSION"

#: Имя версии — это «1.4», «v1.4», «1.4.2»; всё остальное подозрительно.
_VERSION_OK = re.compile(r"^v?\d+(\.\d+){0,2}(-[\w.]+)?$")

#: «v1.2-3-gabc1234» — это «три коммита после v1.2», но читать это невозможно.
_DESCRIBE = re.compile(r"^(?P<tag>.+?)-(?P<ahead>\d+)-g[0-9a-f]{4,}$")


def pretty_version(raw: str) -> str:
    """Приводит вывод git describe к человеческому виду: v1.2+3."""
    match = _DESCRIBE.match(raw.strip())
    if not match:
        return raw.strip()
    return f"{match.group('tag')}+{match.group('ahead')}"


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

    async def version(self, ref: str = "HEAD") -> str:
        """Имя версии: файл VERSION, иначе ближайший тег, иначе пусто.

        Имя человек может назвать вслух и запомнить, хеш — только сверить.
        Поэтому «v1.2 → v1.3»; хеши остаются запасным вариантом, и на них всё
        по-прежнему работает, если имени взять неоткуда.
        """
        named = await self._named_version(ref)
        if named:
            return named

        code, output = await run_command(
            ["git", "describe", "--tags", "--abbrev=7", ref], self.root, GIT_TIMEOUT
        )
        return pretty_version(output) if code == 0 else ""

    async def _named_version(self, ref: str) -> str:
        if ref == "HEAD":
            try:
                raw = (self.root / VERSION_FILE).read_text(encoding="utf-8")
            except OSError:
                raw = ""
        else:
            code, raw = await run_command(
                ["git", "show", f"{ref}:{VERSION_FILE}"], self.root, GIT_TIMEOUT
            )
            raw = raw if code == 0 else ""

        name = raw.strip().splitlines()[0].strip() if raw.strip() else ""
        if not _VERSION_OK.match(name):
            return ""
        return name if name.startswith("v") else f"v{name}"

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
        # теги тянем отдельно: без них не из чего собрать имя версии
        await run_command(
            ["git", "fetch", "--quiet", "--tags", "origin"], self.root, GIT_TIMEOUT
        )
        behind_raw = await self._git("rev-list", "--count", f"HEAD..{upstream}")
        messages = await self._git("log", "--format=%s", f"HEAD..{upstream}")

        return UpdateStatus(
            branch=branch,
            local=await self.commit(),
            remote=await self.commit(upstream),
            behind=int(behind_raw or 0),
            messages=[line for line in messages.splitlines() if line.strip()],
            local_name=await self.version(),
            remote_name=await self.version(upstream),
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

    def test_modules(self) -> list[str]:
        """Что запускать, по кускам. Кусок — тест-класс, а не файл целиком.

        Файлы очень разные: в самом большом полсотни тестов, и пока он идёт,
        остальные процессы стоят без дела. Классы дробятся мельче, и прогон
        упирается уже не в один файл, а в общее число тестов.
        """
        units: list[str] = []
        for path in sorted((self.root / "tests").glob("test_*.py")):
            module = f"tests.{path.stem}"
            classes = self._classes(path)
            units.extend(f"{module}.{name}" for name in classes)
            if not classes:
                units.append(module)  # ни одного класса не нашли — берём файлом
        return units

    @staticmethod
    def _classes(path: Path) -> dict[str, int]:
        """Классы с тестами и сколько тестов в каждом — без импорта файла."""
        found: dict[str, int] = {}
        current = ""
        inside_string = False
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        for line in lines:
            # в тестах обновлятеля лежат целые файлы тестов строками — их
            # классы к делу не относятся
            if line.count('"""') % 2:
                inside_string = not inside_string
                continue
            if inside_string:
                continue
            match = _CLASS_LINE.match(line)
            if match:
                current = match.group(1)
                found.setdefault(current, 0)
            elif current and _TEST_LINE.match(line):
                found[current] += 1
        # классы-заготовки без собственных тестов запускать нечего
        return {name: count for name, count in found.items() if count}

    def _weight(self, unit: str) -> int:
        """Сколько тестов в куске — по ним и раскладываем."""
        module, _, klass = unit.rpartition(".")
        if not klass or not module.startswith("tests"):
            return 1
        path = self.root / (module.replace(".", "/") + ".py")
        if not path.exists():  # это модуль целиком, а не класс
            path = self.root / (unit.replace(".", "/") + ".py")
            return sum(self._classes(path).values()) or 1
        return self._classes(path).get(klass, 1)

    def _shards(self, modules: Sequence[str]) -> list[list[str]]:
        """Делит тесты между процессами: по ядру на процесс, но не больше
        четырёх — дальше выигрыш съедается запуском самого питона.

        Раскладываем от большего к меньшему в самую свободную стопку: иначе
        один длинный модуль достаётся процессу, который и так загружен, и
        остальные ждут его в конце.
        """
        workers = max(1, min(os.cpu_count() or 1, MAX_TEST_WORKERS, len(modules)))
        if workers == 1:
            return [list(modules)]

        groups: list[list[str]] = [[] for _ in range(workers)]
        loads = [0] * workers
        for module in sorted(modules, key=self._weight, reverse=True):
            lightest = loads.index(min(loads))
            groups[lightest].append(module)
            loads[lightest] += self._weight(module)
        return [group for group in groups if group]

    async def _run_tests(self) -> tuple[bool, str]:
        """Прогон тестов. На старой машине полный набор идёт минутами,
        поэтому куски раздаются нескольким процессам разом."""
        modules = self.test_modules()
        if not modules:
            return await self._run_tests_in_one_go()

        results = await asyncio.gather(
            *(
                run_command(
                    [self.python, "-m", "unittest", *shard], self.root, TESTS_TIMEOUT
                )
                for shard in self._shards(modules)
            ),
            return_exceptions=True,
        )

        failures: list[str] = []
        for result in results:
            if isinstance(result, BaseException):
                raise result
            code, output = result
            if code != 0:
                failures.extend(output.strip().splitlines()[-6:])
        return not failures, "\n".join(failures[-8:])

    async def _run_tests_in_one_go(self) -> tuple[bool, str]:
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
        # именами версий, а не хешами: их человек и увидит в сообщении
        was, now = status.here, await self.version() or await self.commit()
        return UpdateResult(
            True,
            f"Обновился: <code>{was}</code> → <code>{now}</code> "
            f"({status.behind} "
            + plural(status.behind, "коммит", "коммита", "коммитов")
            + "). Перезапускаюсь.",
            restart=True,
        )

    async def _rollback(self, commit: str) -> None:
        try:
            await self._git("reset", "--hard", commit)
        except UpdateError:
            logger.exception("Не смог откатиться на %s", commit)


class UpdateWatcher:
    """Следит, не появилось ли новой версии, и делает то, о чём просили.

    Два режима. «Сказать» — приходит сообщение с кнопкой, решение за человеком:
    молча тянуть код на машину, к которой хозяин не подходил, — вообще-то
    плохая идея. «Поставить» — бот обновляется сам; это осмысленно ровно
    потому, что перед перезапуском он прогоняет тесты и откатывается, если
    они не сошлись, — то есть кнопка «Обновить» и так была формальностью.

    Про один и тот же коммит беспокоим ровно раз.
    """

    def __init__(
        self,
        bot,
        db,
        updater: "Updater",
        owner_id: Optional[int] = None,
        interval_hours: float = 6,
        installer: Optional[Callable[[int, UpdateStatus], Awaitable[None]]] = None,
    ) -> None:
        self._bot = bot
        self._db = db
        self._updater = updater
        self._owner_id = owner_id
        self._interval = max(30.0, interval_hours * 3600)
        self._installer = installer
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

        if self._installer is not None:
            # ставим сами — но пометку кладём заранее: если обновление сорвётся,
            # не хочется пытаться каждые пять минут по кругу
            await self._db.set_meta("notified_commit", status.remote)
            await self._installer(owner, status)
            return True

        from .keyboards import update_actions  # локально: иначе кольцо импортов

        lines = [
            f"🆕 <b>Вышло обновление бота</b>: {status.behind} "
            + plural(status.behind, "коммит", "коммита", "коммитов"),
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
