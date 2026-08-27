"""Строка раз в час: бот жив, вот сколько было дел и что будет дальше.

Окно с ботом стоит открытым сутками, и в спокойный день в нём не появляется
ничего. Отличить «всё хорошо, никто не писал» от «повисло» в таком окне
невозможно, поэтому раз в час бот сам отмечается — коротко и в одну строку.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .db import Database
from .formatting import plural
from .journal import Counter
from .middlewares import now_for
from .reminders import next_fire, wait_text

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 3600


def uptime_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} {plural(days, 'день', 'дня', 'дней')} {hours} ч"
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    return f"{minutes} мин"


class Heartbeat:
    """Раз в час пишет в консоль строку о том, что бот на месте."""

    def __init__(
        self,
        db: Database,
        counter: Counter,
        interval_seconds: int = INTERVAL_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self._db = db
        self._counter = counter
        self._interval = interval_seconds
        self._clock = clock
        self._started = clock()
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="heartbeat")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                logger.info("%s", await self.line())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - отметка о жизни не должна ронять бота
                logger.debug("Не смог отметиться", exc_info=True)

    async def line(self) -> str:
        """Что именно написать. Отдельно от таймера — чтобы было что проверять."""
        parts = [f"на месте {uptime_text(self._clock() - self._started)}"]

        events = self._counter.take()
        parts.append(
            f"за час {events} {plural(events, 'событие', 'события', 'событий')}"
            if events
            else "за час тихо"
        )

        owner = await self._db.owner_id()
        if owner is not None:
            settings = await self._db.ensure_user(owner)
            now = now_for(settings.tz)
            upcoming = next_fire(
                [item.at for item in await self._db.list_reminders(owner)], now
            )
            if upcoming is not None:
                at, wait = upcoming
                parts.append(f"дальше {at:%H:%M} ({wait_text(wait)})")

        return " · ".join(parts)
