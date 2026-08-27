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

HINT = (
    "Соединение с Telegram рвётся и восстанавливается — обычно так ведёт себя "
    "фильтрация трафика у провайдера. Бот работает: сообщения дойдут, "
    "напоминания придут. Если хочется ровной связи — VPN."
)


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

    @staticmethod
    def looks_like_hiccup(record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if not any(marker in message for marker in HICCUP_MARKERS):
            return False
        if "Connection established" in message or "Sleep for" in message:
            return True
        return any(error in message for error in NETWORK_ERRORS)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.looks_like_hiccup(record):
            return True

        now = self._clock()
        first_time = self._last_report is None
        if first_time or now - self._last_report >= self._quiet:
            self._rewrite(record, first_time)
            self._last_report = now
            self._suppressed = 0
            return True

        self._suppressed += 1
        return False

    def _rewrite(self, record: logging.LogRecord, first_time: bool) -> None:
        """Ошибка библиотеки превращается в понятное предупреждение."""
        if first_time:
            text = f"⚠️ {HINT}"
        else:
            minutes = max(1, round(self._quiet / 60))
            times = self._suppressed + 1
            text = (
                f"⚠️ Связь с Telegram рвалась ещё {times} "
                f"{plural(times, 'раз', 'раза', 'раз')} за последние {minutes} мин. "
                "Бот работает."
            )
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
