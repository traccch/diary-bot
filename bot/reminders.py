"""Планировщик напоминаний: раз в полминуты проверяет, кому пора мерить давление.

Ничего не хранит в памяти — состояние живёт в БД, поэтому перезапуск бота
не приводит ни к потерянным, ни к задвоенным напоминаниям.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .db import Database
from .keyboards import reminder_actions

logger = logging.getLogger(__name__)

#: Насколько поздно ещё уместно напомнить (бот мог быть выключен).
GRACE_MINUTES = 20
#: Если измерение сделано недавно, напоминание пропускается.
SKIP_WINDOW_MINUTES = 90
#: Пауза кнопки «отложить».
SNOOZE_MINUTES = 15

TICK_SECONDS = 30

REMINDER_TEXT = (
    "⏰ <b>Пора измерить давление</b>\n\n"
    "Посиди спокойно 5 минут, манжета на уровне сердца — и пришли цифры, "
    "например <code>120/80 68</code>."
)

SNOOZE_TEXT = (
    "⏰ <b>Напоминаю ещё раз</b>\n\nПришли измерение, например <code>120/80 68</code>."
)


def local_now(tz: str, now_utc: dt.datetime) -> dt.datetime:
    """Локальное время пользователя без таймзоны — в нём живёт весь дневник."""
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return now_utc.astimezone(zone).replace(tzinfo=None)


def is_due(
    now: dt.datetime,
    at: dt.time,
    last_fired_on: Optional[dt.date],
    grace_minutes: int = GRACE_MINUTES,
) -> bool:
    """Пора ли отправлять напоминание, назначенное на `at` по местному времени."""
    if last_fired_on == now.date():
        return False
    scheduled = dt.datetime.combine(now.date(), at)
    late = (now - scheduled).total_seconds() / 60
    return 0 <= late <= grace_minutes


class ReminderScheduler:
    def __init__(self, bot: Bot, db: Database, tick_seconds: int = TICK_SECONDS) -> None:
        self._bot = bot
        self._db = db
        self._tick_seconds = tick_seconds
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="reminders")

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
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - планировщик не должен падать целиком
                logger.exception("Сбой при рассылке напоминаний")
            await asyncio.sleep(self._tick_seconds)

    async def tick(self, now_utc: Optional[dt.datetime] = None) -> int:
        """Один проход планировщика. Возвращает число отправленных сообщений."""
        now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
        sent = 0

        for user_id in await self._db.pop_due_snoozes(now_utc.replace(tzinfo=None)):
            sent += await self._send(user_id, SNOOZE_TEXT)

        for candidate in await self._db.due_candidates():
            now = local_now(candidate.tz, now_utc)
            if not is_due(now, candidate.at, candidate.last_fired_on):
                continue

            await self._db.mark_reminder_fired(candidate.reminder_id, now.date())
            if candidate.skip_if_measured:
                since = now - dt.timedelta(minutes=SKIP_WINDOW_MINUTES)
                if await self._db.has_measurement_since(candidate.user_id, since):
                    logger.debug("Напоминание %s пропущено: замер уже есть", candidate.at)
                    continue
            sent += await self._send(candidate.user_id, REMINDER_TEXT)

        return sent

    async def _send(self, user_id: int, text: str) -> int:
        try:
            await self._bot.send_message(user_id, text, reply_markup=reminder_actions())
        except TelegramAPIError as exc:
            logger.warning("Не отправил напоминание %s: %s", user_id, exc)
            return 0
        return 1
