"""Роутеры и общие команды раздела «Давление»."""

from __future__ import annotations

import datetime as dt

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from ...db import Database, UserSettings
from . import entry, export, reports


def build_router() -> Router:
    """Только то, что принадлежит разделу: /add, /cancel и кнопки."""
    router = Router(name="pressure")
    router.include_router(reports.router)
    router.include_router(export.router)
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
    """Общая команда в разделе «Давление». False — такой команды здесь нет."""
    if name in {"stats", "report"}:
        await reports.cmd_stats(message, db, user, now)
    elif name in {"chart", "graph"}:
        await reports.cmd_chart(message, db, user, now)
    elif name == "last":
        await entry.cmd_last(message, db, user, now)
    elif name == "undo":
        await entry.cmd_undo(message, db, user)
    elif name == "del":
        await entry.cmd_del(message, command, db, user)
    elif name in {"export", "doctor", "pdf"}:
        await export.cmd_export(message, db, user)
    else:
        return False
    return True


__all__ = ["build_router", "handle_command"]
