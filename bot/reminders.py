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

from . import prompts, sections
from .db import Database
from .keyboards import health_prompt, reminder_actions

logger = logging.getLogger(__name__)

#: Насколько поздно ещё уместно напомнить (бот мог быть выключен).
GRACE_MINUTES = 20
#: Если измерение сделано недавно, напоминание пропускается.
SKIP_WINDOW_MINUTES = 90
#: Пауза кнопки «отложить».
SNOOZE_MINUTES = 15

TICK_SECONDS = 30

#: Тексты напоминаний по темам: у давления и денег они разные.
REMINDER_TEXTS = {
    sections.PRESSURE: (
        "⏰ <b>Пора измерить давление</b>\n\n"
        "Посиди спокойно 5 минут, манжета на уровне сердца — и пришли цифры, "
        "например <code>120/80 68</code>."
    ),
    sections.MONEY: (
        "⏰ <b>Записать траты за день</b>\n\n"
        "Пока помнишь — пришли одной строкой: <code>кофе 300</code>, "
        "<code>такси 450</code>."
    ),
    sections.ENGLISH: (
        "⏰ <b>Пять минут английского</b>\n\n"
        "Десять карточек — три минуты. Регулярность здесь важнее объёма: "
        "короткий заход каждый день держит слова живыми."
    ),
}
#: У самочувствия текст не постоянный: вопрос выбирается на месте, см. prompts.

SNOOZE_TEXTS = {
    sections.PRESSURE: (
        "⏰ <b>Напоминаю ещё раз</b>\n\nПришли измерение, например <code>120/80 68</code>."
    ),
    sections.MONEY: (
        "⏰ <b>Напоминаю ещё раз</b>\n\nЗапиши траты: <code>кофе 300</code>."
    ),
    sections.ENGLISH: (
        "⏰ <b>Напоминаю ещё раз</b>\n\nПять минут английского — /eng."
    ),
}


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

        for user_id, topic in await self._db.pop_due_snoozes(now_utc.replace(tzinfo=None)):
            if topic == sections.HEALTH:
                sent += await self._ask_health(user_id, None, now_utc)
                continue
            sent += await self._send(user_id, SNOOZE_TEXTS.get(topic, SNOOZE_TEXTS[sections.PRESSURE]), topic)

        for candidate in await self._db.due_candidates():
            now = local_now(candidate.tz, now_utc)
            if not is_due(now, candidate.at, candidate.last_fired_on):
                continue

            await self._db.mark_reminder_fired(candidate.reminder_id, now.date())
            if candidate.skip_if_measured and await self._already_done(candidate, now):
                logger.debug(
                    "Напоминание %s (%s) пропущено: сегодня уже сделано",
                    candidate.at,
                    candidate.topic,
                )
                continue
            if candidate.topic == sections.HEALTH:
                sent += await self._ask_health(candidate.user_id, candidate.at, now_utc)
                continue

            text = REMINDER_TEXTS.get(candidate.topic, REMINDER_TEXTS[sections.PRESSURE])
            sent += await self._send(candidate.user_id, text, candidate.topic)

        return sent

    async def _ask_health(
        self, user_id: int, at: Optional[dt.time], now_utc: dt.datetime
    ) -> int:
        """Мягкий вопрос про самочувствие — или молчание, если спрашивать нечего.

        Молчание здесь не оплошность, а условие всей затеи: вопрос, который
        приходит и тогда, когда ответ уже записан, перестают читать.
        """
        settings = await self._db.ensure_user(user_id)
        now = local_now(settings.tz, now_utc)
        already = await self._db.metrics_on(user_id, now.date())
        prompt = prompts.pick(at or now.time(), now.date(), already)
        if prompt is None:
            logger.debug("Про самочувствие сегодня спрашивать нечего: %s", user_id)
            return 0
        return await self._send(
            user_id, prompts.render(prompt), sections.HEALTH, health_prompt(prompt)
        )

    async def _already_done(self, candidate, now: dt.datetime) -> bool:
        """Дёргать человека, когда он уже всё сделал, — вернейший способ
        научить его не замечать напоминания."""
        if candidate.topic == sections.PRESSURE:
            since = now - dt.timedelta(minutes=SKIP_WINDOW_MINUTES)
            return await self._db.has_measurement_since(candidate.user_id, since)
        if candidate.topic == sections.MONEY:
            return await self._db.has_transaction_on(candidate.user_id, now.date())
        if candidate.topic == sections.ENGLISH:
            return await self._db.eng_practiced_since(candidate.user_id, now.date())
        return False

    async def _send(
        self,
        user_id: int,
        text: str,
        topic: str = sections.PRESSURE,
        reply_markup=None,
    ) -> int:
        try:
            await self._bot.send_message(
                user_id, text, reply_markup=reply_markup or reminder_actions(topic)
            )
        except TelegramAPIError as exc:
            logger.warning("Не отправил напоминание %s: %s", user_id, exc)
            return 0
        return 1
