"""Обновление бота из GitHub: /version, /update и кнопка под ними.

Команды доступны только владельцу: обновление запускает на его машине код,
который приехал из репозитория, и посторонним такой рычаг ни к чему.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..db import Database
from ..formatting import esc
from ..keyboards import update_actions
from ..updater import UpdateError, Updater

router = Router(name="update")
logger = logging.getLogger(__name__)

NOT_OWNER = "Эта команда только для владельца бота."

WORKING = (
    "⏳ Обновляюсь: забираю код, проверяю зависимости и прогоняю тесты.\n"
    "<i>Может занять пару минут — если менялись библиотеки, дольше.</i>"
)


async def is_owner(db: Database, owner_id: Optional[int], user_id: int) -> bool:
    """Владелец — тот, кто указан в .env, иначе первый написавший боту."""
    if owner_id is not None:
        return user_id == owner_id
    stored = await db.owner_id()
    return stored is None or stored == user_id


def render_status(status) -> str:
    if not status.available:
        return (
            f"✅ <b>Последняя версия</b>\n"
            f"<code>{esc(status.local)}</code> · ветка {esc(status.branch)}"
        )

    lines = [
        f"🆕 <b>Есть обновление</b>: {status.behind} "
        + ("коммит" if status.behind == 1 else "коммитов"),
        f"<code>{esc(status.local)}</code> → <code>{esc(status.remote)}</code>",
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
    await callback.message.answer(WORKING)

    try:
        result = await updater.apply()
    except UpdateError as exc:
        await callback.message.answer(f"⚠️ {esc(str(exc))}")
        return
    except Exception:  # noqa: BLE001 - обновление не должно ронять бота
        logger.exception("Обновление сорвалось")
        await callback.message.answer(
            "⚠️ Обновление сорвалось, подробности в логах. Бот работает как работал."
        )
        return

    await callback.message.answer(("✅ " if result.ok else "⚠️ ") + result.message)
    if result.restart:
        await db.set_meta("notified_commit", "")
        restart_event.set()
