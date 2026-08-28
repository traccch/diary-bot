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
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..db import Database
from ..formatting import duration, esc, plural
from ..keyboards import update_actions
from ..updater import STEP_ORDER, STEP_TITLES, UpdateError, Updater

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


def render_progress(current: Optional[str], seen: set[str], elapsed: float) -> str:
    """Список шагов: сделанное — галочкой, текущее — с секундомером.

    Секундомер здесь не украшение: тесты идут полминуты, зависимости — минуты,
    и без бегущих секунд «⏳ Обновляюсь» неотличимо от зависшего бота.
    """
    reached = False
    lines = ["⏳ <b>Обновляюсь</b>", ""]
    for step, title in STEPS:
        if step == current:
            reached = True
            lines.append(f"⏳ {title}… <i>{duration(elapsed)}</i>")
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
        self._shown = ""
        self._ticker: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        self._sent = await self._message.answer(render_progress(None, set(), 0))
        self._ticker = asyncio.create_task(self._tick(), name="update-progress")

    async def __call__(self, step: str) -> None:
        self._current = step
        self._seen.add(step)
        self._started = time.monotonic()
        await self._draw()

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
        await self._sent.edit_text(f"{text}\n<i>Заняло {duration(self.total)}.</i>")

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat)
            await self._draw()

    async def _draw(self) -> None:
        elapsed = time.monotonic() - self._started
        text = render_progress(self._current, self._seen, elapsed)
        if text == self._shown:
            return
        try:
            await self._sent.edit_text(text)
        except TelegramBadRequest:
            pass  # то же самое сообщение Telegram считает ошибкой
        self._shown = text


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
    db: Database,
    updater: Updater,
    owner_id: Optional[int] = None,
) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not await is_owner(db, owner_id, user_id):
        await message.answer(NOT_OWNER)
        return

    try:
        status = await updater.check()
    except UpdateError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return

    await message.answer(
        render_status(status),
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
