"""Мидлварь: настройки пользователя и его локальные «сейчас» и «сегодня»."""

from __future__ import annotations

import datetime as dt
from typing import Any, Awaitable, Callable, Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from .db import Database


def now_for(tz: str) -> dt.datetime:
    """Текущее время в часовом поясе пользователя, без tzinfo."""
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return dt.datetime.now(zone).replace(tzinfo=None, second=0, microsecond=0)


class UserMiddleware(BaseMiddleware):
    """Гарантирует, что пользователь есть в БД, и кладёт его настройки в data."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        db: Database = data["db"]
        settings = await db.ensure_user(user.id)
        now = now_for(settings.tz)
        data["user"] = settings
        data["now"] = now
        data["today"] = now.date()
        return await handler(event, data)
