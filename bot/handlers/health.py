"""Ответы на вопросы о самочувствии: одно нажатие — одна записанная цифра.

Здесь нет ни состояний, ни диалога: кнопка несёт в себе и показатель, и
значение, поэтому ответить можно даже на вчерашнее сообщение, а пропустить —
не отвечая вовсе.
"""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from .. import prompts
from ..db import Database, UserSettings

router = Router(name="health")

SKIPPED = "Хорошо, не сегодня."


async def _replace(callback: CallbackQuery, text: str) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(text, reply_markup=None)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "hm:skip")
async def cb_skip(callback: CallbackQuery) -> None:
    await callback.answer("Пропустил")
    await _replace(callback, SKIPPED)


@router.callback_query(F.data.startswith("hm:"))
async def cb_answer(
    callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    _, kind, raw = (callback.data or "").split(":", 2)
    value = prompts.clean(kind, raw)
    if value is None:
        await callback.answer("Не понял это значение", show_alert=True)
        return

    await db.set_metric(user.user_id, kind, now.date(), value)
    await callback.answer("Записал")
    await _replace(callback, prompts.confirm(kind, value))
