"""Журнал событий: одна строка на каждое сообщение — вместо служебного шума.

Aiogram пишет про каждое сообщение «Update id=287479777 is handled. Duration
2855 ms» — из этого не понять ни кто написал, ни что именно. Здесь та же
строка, но по-человечески:

    23:11:13 · Михаил → «120/80 68» · 1,2 с

Текст в строке обрезан до шестидесяти знаков: журнал — это «что происходит»,
а не копия переписки. Полное сообщение всегда есть в самом Telegram.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)

#: Длиннее в строку не влезает, да и не нужно.
MAX_TEXT = 60


def short(text: str, limit: int = MAX_TEXT) -> str:
    """Однострочная выжимка: переносы схлопнуты, длинное обрезано."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def describe(event: TelegramObject) -> str:
    """Что произошло — коротко и понятно человеку, а не отладчику."""
    if isinstance(event, CallbackQuery):
        return f"кнопка {short(event.data or '—', 30)}"

    if not isinstance(event, Message):
        return type(event).__name__

    if event.voice is not None:
        return f"голосовое, {event.voice.duration or '?'} с"
    if event.document is not None:
        return f"файл {short(event.document.file_name or 'без имени', 30)}"
    if event.photo:
        return "фотография"
    text = (event.text or event.caption or "").strip()
    if not text:
        return "сообщение без текста"
    return text if text.startswith("/") else f"«{short(text)}»"


def took(seconds: float) -> str:
    return f"{seconds:.1f} с".replace(".", ",") if seconds >= 1 else f"{seconds * 1000:.0f} мс"


def who(event: TelegramObject) -> str:
    user = getattr(event, "from_user", None)
    return getattr(user, "first_name", None) or "кто-то"


class Counter:
    """Сколько событий было с прошлого раза — для часовой строки о жизни."""

    def __init__(self) -> None:
        self.total = 0
        self._since_last = 0

    def add(self) -> None:
        self.total += 1
        self._since_last += 1

    def take(self) -> int:
        """Отдаёт накопленное и обнуляет счёт."""
        count, self._since_last = self._since_last, 0
        return count


class JournalMiddleware(BaseMiddleware):
    """Пишет строку про каждое обработанное событие — и про сорвавшееся тоже."""

    def __init__(self, counter: Counter | None = None) -> None:
        super().__init__()
        self.counter = counter or Counter()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        started = time.monotonic()
        self.counter.add()
        try:
            result = await handler(event, data)
        except Exception:
            logger.exception(
                "%s → %s · сорвалось за %s",
                who(event),
                describe(event),
                took(time.monotonic() - started),
            )
            raise
        logger.info(
            "%s → %s · %s", who(event), describe(event), took(time.monotonic() - started)
        )
        return result
