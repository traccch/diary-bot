"""Роутеры и общие команды раздела «Деньги»."""

from __future__ import annotations

import datetime as dt

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from ...db import Database, UserSettings
from . import categories, entry, reports


def build_router() -> Router:
    """Только то, что принадлежит разделу: категории, лимиты, /balance, кнопки."""
    router = Router(name="money")
    router.include_router(categories.router)
    router.include_router(reports.router)
    router.include_router(entry.router)
    return router


async def handle_command(
    name: str,
    message: Message,
    command: CommandObject,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
) -> bool:
    """Общая команда в разделе «Деньги». False — такой команды здесь нет."""
    today = now.date()
    if name in {"stats", "report"}:
        await reports.cmd_stats(message, db, user, today)
    elif name == "last":
        await entry.cmd_last(message, db, user, today)
    elif name == "undo":
        await entry.cmd_undo(message, db, user)
    elif name == "del":
        await entry.cmd_del(message, command, db, user)
    elif name == "export":
        await entry.cmd_export(message, db, user, today)
    else:
        return False
    return True


__all__ = ["build_router", "handle_command"]
