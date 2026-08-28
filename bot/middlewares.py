"""Мидлвари: кого пускать к боту и что положить в данные обработчика."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Awaitable, Callable, Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from .db import Database

logger = logging.getLogger(__name__)


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


class AccessMiddleware(BaseMiddleware):
    """Пускает к боту только хозяина и тех, кого он назвал.

    Личный дневник — не место для посторонних, а найти бота в Telegram может
    кто угодно: имена перебираются. Записи чужому и так не видны (всё в базе
    разложено по user_id), но и заводить ему дневник в чужом боте незачем:
    это чужой диск, чужой процесс и чужие напоминания.

    Кто хозяин: тот, кто указан в OWNER_ID, иначе — тот, кто написал первым.
    """

    def __init__(self, allowed: frozenset[int] | set[int] = frozenset()) -> None:
        super().__init__()
        self._allowed = frozenset(allowed)
        #: Кому уже объяснили, что бот личный: повторяться незачем.
        self._told: set[int] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user") or getattr(
            event, "from_user", None
        )
        if user is None or user.is_bot:
            return await handler(event, data)

        if await self.allowed(user.id, data["db"]):
            return await handler(event, data)

        logger.warning(
            "Чужой стучится: %s (id %s) — не пустил", user.full_name, user.id
        )
        if user.id not in self._told:
            self._told.add(user.id)
            await self.refuse(event)
        return None

    async def allowed(self, user_id: int, db: Database) -> bool:
        if user_id in self._allowed:
            return True
        if self._allowed:
            return False  # список задан явно — значит, он и решает
        owner = await db.owner_id()
        return owner is None or owner == user_id

    @staticmethod
    async def refuse(event: TelegramObject) -> None:
        """Короткий отказ без подробностей: чужому знать про бота нечего."""
        answer = getattr(event, "answer", None)
        if answer is None:
            return
        try:
            await answer("Это личный дневник. Доступ закрыт.")
        except Exception:  # noqa: BLE001 - вежливость не стоит того, чтобы падать
            logger.debug("Не смог ответить чужому", exc_info=True)
