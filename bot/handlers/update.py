"""Обновление бота из GitHub: /version, /update и кнопка под ними.

Команды доступны только владельцу: обновление запускает на его машине код,
который приехал из репозитория, и посторонним такой рычаг ни к чему.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from ..db import Database
from .. import sysinfo
from ..formatting import duration, esc, plural
from ..keyboards import update_actions
from ..updater import STEP_ORDER, STEP_SHORT, STEP_TITLES, UpdateError, Updater

router = Router(name="update")
logger = logging.getLogger(__name__)

NOT_OWNER = "Эта команда только для владельца бота."

#: Пометка «после перезапуска доложись вот сюда».
RESTART_NOTICE = "restart_notice"

#: Шаги обновления в том порядке, в каком их видит человек.
STEPS: tuple[tuple[str, str], ...] = tuple(
    (step, STEP_TITLES[step]) for step in STEP_ORDER
)

#: Как часто дорисовывать счётчик секунд у текущего шага.
HEARTBEAT_SECONDS = 15


def render_progress(
    current: Optional[str], seen: set[str], elapsed: float, load: str = ""
) -> str:
    """Список шагов: сделанное — галочкой, текущее — с секундомером.

    Секундомер здесь не украшение: тесты идут полминуты, зависимости — минуты,
    и без бегущих секунд «⏳ Обновляюсь» неотличимо от зависшего бота.
    """
    reached = False
    lines = ["⏳ <b>Обновляюсь</b>", ""]
    for step, title in STEPS:
        if step == current:
            reached = True
            tail = f"{duration(elapsed)}" + (f" · {load}" if load else "")
            lines.append(f"⏳ {title}… <i>{tail}</i>")
        elif reached:
            lines.append(f"◦ {title}")
        elif step in seen:
            lines.append(f"✅ {title}")
        elif current is None:
            lines.append(f"◦ {title}")
        else:
            # шаг остался позади нетронутым — например, зависимости не менялись
            lines.append(f"⏭ {title} <i>— не понадобилось</i>")
    lines.append("")
    lines.append(
        "<i>Если тесты не сойдутся, верну прежнюю версию сам. Писать можно и "
        "сейчас — отвечу, как только поднимусь.</i>"
    )
    return "\n".join(lines)


class ProgressReport:
    """Одно сообщение, которое переписывается по ходу обновления."""

    def __init__(self, message: Message, heartbeat: int = HEARTBEAT_SECONDS) -> None:
        self._message = message
        self._heartbeat = heartbeat
        self._current: Optional[str] = None
        self._seen: set[str] = set()
        self._opened = time.monotonic()  # начало всего обновления
        self._started = time.monotonic()  # начало текущего шага
        self._spent: dict[str, float] = {}  # сколько занял каждый шаг
        self._meter = sysinfo.CpuMeter()
        self._meter.sample()  # точка отсчёта для загрузки процессора
        self._load = ""  # что показывал компьютер на последнем замере
        self._peak_cpu = 0
        self._peak_mem = 0
        self._shown = ""
        self._ticker: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        self._sent = await self._message.answer(render_progress(None, set(), 0))
        self._ticker = asyncio.create_task(self._tick(), name="update-progress")

    async def __call__(self, step: str) -> None:
        self._close_step()
        self._current = step
        self._seen.add(step)
        self._started = time.monotonic()
        await self._draw()

    def _close_step(self) -> None:
        if self._current is not None:
            self._spent[self._current] = time.monotonic() - self._started

    def breakdown(self) -> str:
        """Время по шагам: «код 12 с · тесты 2 мин 11 с».

        Без разбивки непонятно, на что ушли минуты, и остаётся только гадать —
        а гадать про собственный компьютер обидно.
        """
        parts = [
            f"{STEP_SHORT[step]} {duration(spent)}"
            for step, spent in self._spent.items()
            if spent >= 1 and step in STEP_SHORT
        ]
        if self._peak_cpu:
            parts.append(f"ЦП до {self._peak_cpu}%")
        if self._peak_mem:
            parts.append(f"память до {self._peak_mem}%")
        return " · ".join(parts)

    @property
    def total(self) -> float:
        return time.monotonic() - self._opened

    async def finish(self, text: str) -> None:
        """Последнее слово: итог и сколько всё заняло.

        Время шага бежало на глазах и пропадало вместе с индикатором — а это
        как раз то, что хочется знать в следующий раз.
        """
        if self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None
        self._close_step()

        tail = f"Заняло {duration(self.total)}"
        details = self.breakdown()
        if details:
            tail += f" · {details}"
        await self._sent.edit_text(f"{text}\n<i>{tail}.</i>")

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat)
            await self._draw()

    def _measure(self) -> None:
        """Замер нагрузки: по нему видно, во что упирается обновление.

        Процессор в потолке — значит, дело в машине и ждать придётся. Он
        свободен, а время идёт — значит, упёрлись в диск или антивирус, и это
        уже чинится.
        """
        busy = self._meter.sample()
        state = sysinfo.memory()
        if busy is not None:
            self._peak_cpu = max(self._peak_cpu, busy)
        if state is not None:
            self._peak_mem = max(self._peak_mem, state.used_percent)

        parts = []
        if busy is not None:
            parts.append(f"ЦП {busy}%")
        if state is not None:
            parts.append(f"память {state.used_percent}%")
        self._load = " · ".join(parts)

    async def _draw(self) -> None:
        self._measure()
        elapsed = time.monotonic() - self._started
        text = render_progress(self._current, self._seen, elapsed, self._load)
        if text == self._shown:
            return
        try:
            await self._sent.edit_text(text)
        except TelegramBadRequest:
            pass  # то же самое сообщение Telegram считает ошибкой
        self._shown = text


async def force_update(
    message: Message,
    db: Database,
    updater: Updater,
    restart_event: Optional[asyncio.Event] = None,
) -> None:
    """Обновление без тестов — когда тесты сами и мешают.

    Обычно откат на красных тестах спасает. Но если не сходится сам тест — не
    код, а проверка, — обновления перестают приезжать вовсе, и починка тоже.
    Решение оставляем человеку и говорим прямо, чем он рискует.

    Заодно это единственный способ обновиться, когда в папке правки: они
    уезжают в git stash — не потеряются, но и дорогу не перекроют.
    """
    changed = await updater.local_changes()
    if changed:
        try:
            await updater.stash_local()
        except UpdateError as exc:
            await message.answer(f"⚠️ Не смог отложить правки: {esc(str(exc))}")
            return
        await message.answer(
            f"📦 Отложил правки в <code>git stash</code> "
            f"({len(changed)} {plural(len(changed), 'файл', 'файла', 'файлов')}). "
            "Вернуть их: <code>git stash pop</code>."
        )

    report = ProgressReport(message)
    await report.start()
    try:
        result = await updater.apply(run_tests=False, progress=report)
    except Exception:  # noqa: BLE001
        logger.exception("Обновление без тестов сорвалось")
        await report.finish("⚠️ Не вышло. Бот работает как работал.")
        return

    await report.finish(
        ("✅ " if result.ok else "⚠️ ") + result.message + "\n<i>Тесты не гонял.</i>"
    )
    if result.restart and restart_event is not None:
        await db.set_meta("notified_commit", "")
        await db.set_meta(RESTART_NOTICE, f"{message.chat.id}|{time.time():.0f}")
        restart_event.set()


async def is_owner(db: Database, owner_id: Optional[int], user_id: int) -> bool:
    """Владелец — тот, кто указан в .env, иначе первый написавший боту."""
    if owner_id is not None:
        return user_id == owner_id
    stored = await db.owner_id()
    return stored is None or stored == user_id


def render_status(status) -> str:
    if not status.available:
        return (
            f"✅ <b>Последняя версия</b>: <code>{esc(status.here)}</code>\n"
            f"<i>ветка {esc(status.branch)}</i>"
        )

    lines = [
        f"🆕 <b>Обновление до {esc(status.there)}</b>",
        f"<code>{esc(status.here)}</code> → <code>{esc(status.there)}</code> · "
        + f"{status.behind} "
        + plural(status.behind, "коммит", "коммита", "коммитов"),
        "",
    ]
    lines.extend(f"· {esc(message)}" for message in status.messages[:10])
    if len(status.messages) > 10:
        lines.append(f"<i>…и ещё {len(status.messages) - 10}</i>")
    return "\n".join(lines)


@router.message(Command("version", "update"))
async def cmd_update(
    message: Message,
    command: CommandObject,
    db: Database,
    updater: Updater,
    restart_event: Optional[asyncio.Event] = None,
    owner_id: Optional[int] = None,
) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not await is_owner(db, owner_id, user_id):
        await message.answer(NOT_OWNER)
        return

    if (command.args or "").strip().lower() in {"force", "сила", "без тестов"}:
        await force_update(message, db, updater, restart_event)
        return

    try:
        status = await updater.check()
    except UpdateError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return

    await message.answer(
        render_status(status)
        + (
            "\n\n<i>Если тесты не сходятся сами по себе — <code>/update force</code> "
            "поставит без них.</i>"
            if status.available
            else ""
        ),
        reply_markup=update_actions() if status.available else None,
    )


@router.callback_query(F.data == "upd:apply")
async def cb_apply(
    callback: CallbackQuery,
    db: Database,
    updater: Updater,
    restart_event: asyncio.Event,
    owner_id: Optional[int] = None,
) -> None:
    user_id = callback.from_user.id if callback.from_user else 0
    if not await is_owner(db, owner_id, user_id):
        await callback.answer(NOT_OWNER, show_alert=True)
        return

    await callback.answer("Обновляюсь…")
    if not isinstance(callback.message, Message):
        return

    # отвечаем в исходное сообщение, а не в то, что вернул Telegram:
    # так ответ не зависит от того, привязан ли к ответу экземпляр бота
    await callback.message.edit_reply_markup(reply_markup=None)
    report = ProgressReport(callback.message)
    await report.start()

    try:
        result = await updater.apply(progress=report)
    except UpdateError as exc:
        await report.finish(f"⚠️ {esc(str(exc))}")
        return
    except Exception:  # noqa: BLE001 - обновление не должно ронять бота
        logger.exception("Обновление сорвалось")
        await report.finish(
            "⚠️ Обновление сорвалось, подробности в логах. Бот работает как работал."
        )
        return

    await report.finish(("✅ " if result.ok else "⚠️ ") + result.message)
    if result.restart:
        await db.set_meta("notified_commit", "")
        # чтобы после перезапуска бот сам сказал, что вернулся: иначе человек
        # сидит и ждёт ответа у сообщения «Перезапускаюсь»
        await db.set_meta(
            RESTART_NOTICE,
            f"{callback.message.chat.id}|{time.time():.0f}",
        )
        restart_event.set()
