"""Роутеры и общие команды раздела «Английский»."""

from __future__ import annotations

import datetime as dt

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from ...db import Database, UserSettings
from .. import lookup
from . import progress, quest, session


def build_router() -> Router:
    router = Router(name="english")
    router.include_router(session.router)
    router.include_router(quest.router)
    router.include_router(progress.router)
    router.include_router(lookup.router)
    return router


async def handle_command(
    name: str,
    message: Message,
    command: CommandObject,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
) -> bool:
    """Общая команда в разделе «Английский». False — такой команды здесь нет."""
    if name in {"stats", "report"}:
        await progress.show_progress(message, db, user, now.date())
        return True
    return False


__all__ = ["build_router", "handle_command", "session", "quest", "progress"]
