"""Спокойный лог, когда связь с Telegram рвётся.

Через фильтрующие сети (а в России сейчас почти любой канал до Telegram
такой) простаивающее соединение обрывают примерно раз в полминуты. Aiogram
переподключается сам, но пишет об этом ERROR с трассировкой — окно бота
превращается в стену красного текста, из которой владелец делает вывод, что
всё сломалось. Ломается при этом ничего: сообщения доходят.

Поэтому такие обрывы схлопываются в одну спокойную строку раз в несколько
минут — с числом разрывов и подсказкой. Настоящие ошибки фильтр не трогает.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from .formatting import plural

#: Куски сообщений aiogram, по которым узнаётся обрыв связи.
HICCUP_MARKERS = (
    "Failed to fetch updates",
    "Sleep for",
    "Connection established",
)

#: Ошибки, которые бывают только у сетевых обрывов.
NETWORK_ERRORS = (
    "ServerDisconnectedError",
    "ClientConnectorError",
    "ClientOSError",
    "TelegramNetworkError",
    "ConnectionResetError",
    "TimeoutError",
)

#: Ошибки на той стороне: сеть в порядке, приболел сам Telegram.
SERVER_ERRORS = (
    "TelegramServerError",
    "Bad Gateway",
    "Gateway Timeout",
    "Internal Server Error",
    "TelegramRetryAfter",
)

NETWORK = "network"
SERVER = "server"

HINTS = {
    NETWORK: (
        "Соединение с Telegram рвётся и восстанавливается — обычно так ведёт "
        "себя фильтрация трафика у провайдера. Бот работает: сообщения дойдут, "
        "напоминания придут. Ровнее будет через прокси — TELEGRAM_PROXY в .env."
    ),
    SERVER: (
        "Telegram отвечает ошибкой сервера — это на его стороне, а не у тебя: "
        "ни VPN, ни прокси тут ни при чём. Бот повторяет запрос сам, обычно "
        "проходит за несколько минут."
    ),
}

REPEATS = {
    NETWORK: "Связь с Telegram рвалась ещё {count} {word}",
    SERVER: "Telegram отвечал ошибкой ещё {count} {word}",
}

#: Совместимость со старым именем: раньше подсказка была одна.
HINT = HINTS[NETWORK]


class FlakyNetworkFilter(logging.Filter):
    """Оставляет от череды обрывов одну строку раз в `quiet_seconds`."""

    def __init__(
        self,
        quiet_seconds: float = 300.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        super().__init__()
        self._quiet = quiet_seconds
        self._clock = clock or time.monotonic
        self._last_report: Optional[float] = None
        self._suppressed = 0
        self._cause: Optional[str] = None

    @staticmethod
    def cause(record: logging.LogRecord) -> Optional[str]:
        """Из-за чего строка: сеть, сервер Telegram или это вообще не о том."""
        message = record.getMessage()
        if not any(marker in message for marker in HICCUP_MARKERS):
            return None
        if any(error in message for error in SERVER_ERRORS):
            return SERVER
        if any(error in message for error in NETWORK_ERRORS):
            return NETWORK
        if "Connection established" in message or "Sleep for" in message:
            # продолжение предыдущей истории: чьей она была, мы уже знаем
            return "retry"
        return None

    @classmethod
    def looks_like_hiccup(cls, record: logging.LogRecord) -> bool:
        return cls.cause(record) is not None

    def filter(self, record: logging.LogRecord) -> bool:
        cause = self.cause(record)
        if cause is None:
            return True
        if cause == "retry":
            cause = self._cause or NETWORK  # повтор после чего-то, что уже видели

        now = self._clock()
        first_time = self._last_report is None
        # сменилась причина — это другая история, о ней стоит сказать сразу
        changed = cause != self._cause
        if first_time or changed or now - self._last_report >= self._quiet:
            self._rewrite(record, first_time or changed, cause)
            self._last_report = now
            self._cause = cause
            self._suppressed = 0
            return True

        self._suppressed += 1
        return False

    def _rewrite(self, record: logging.LogRecord, first_time: bool, cause: str) -> None:
        """Ошибка библиотеки превращается в понятное предупреждение."""
        if first_time:
            text = f"⚠️ {HINTS[cause]}"
        else:
            minutes = max(1, round(self._quiet / 60))
            times = self._suppressed + 1
            head = REPEATS[cause].format(
                count=times, word=plural(times, "раз", "раза", "раз")
            )
            text = f"⚠️ {head} за последние {minutes} мин. Бот работает."
        record.msg = text
        record.args = ()
        record.levelno = logging.WARNING
        record.levelname = "WARNING"
        record.exc_info = None
        record.exc_text = None


def install(quiet_seconds: float = 300.0) -> FlakyNetworkFilter:
    """Вешает фильтр на логгеры, через которые aiogram сообщает об обрывах."""
    network_filter = FlakyNetworkFilter(quiet_seconds)
    for name in ("aiogram.dispatcher", "aiogram.event"):
        logging.getLogger(name).addFilter(network_filter)
    return network_filter
