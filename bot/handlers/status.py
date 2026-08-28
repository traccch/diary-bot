"""Состояние бота в чат: то же, что видно в консоли, но с телефона.

Окно с ботом стоит дома, а вопрос «чем он там занят» возникает как раз тогда,
когда до этого окна не дойти.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Optional

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from .. import sysinfo
from ..db import Database, UserSettings
from ..formatting import esc
from ..reminders import next_fire, wait_text
from ..updater import Updater

router = Router(name="status")

#: Между двумя замерами загрузки должно пройти хоть немного времени.
SAMPLE_SECONDS = 0.6


async def load_now() -> str:
    """Мгновенная загрузка: два замера подряд с короткой паузой."""
    meter = sysinfo.CpuMeter()
    meter.sample()
    await asyncio.sleep(SAMPLE_SECONDS)
    return sysinfo.load_line(meter)


async def build_status(
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    updater: Optional[Updater] = None,
    heartbeat=None,
) -> str:
    lines = ["🖥 <b>Состояние</b>"]

    if updater is not None and updater.is_git_repo():
        version = await updater.version() or await updater.commit()
        lines.append(f"Версия: <b>{esc(version)}</b> · ветка {esc(await updater.branch())}")

    machine = sysinfo.describe()
    if machine:
        lines.append(f"Машина: {esc(machine)}")

    load = await load_now()
    if load:
        lines.append(f"Сейчас: <b>{esc(load)}</b>")

    if heartbeat is not None:
        # строку пульса собирали мы сами — экранировать в ней нечего
        lines.append(await heartbeat.line())

    counts = [
        f"давление {await db.count_measurements(user.user_id)}",
        f"траты {await db.count_transactions(user.user_id)}",
        f"пробег {await db.count_readings(user.user_id)}",
    ]
    lines.append("Записей: " + " · ".join(counts))

    reminders = await db.list_reminders(user.user_id)
    upcoming = next_fire([item.at for item in reminders], now)
    if upcoming is not None:
        at, wait = upcoming
        lines.append(f"Ближайшее напоминание: <b>{at:%H:%M}</b> ({wait_text(wait)})")

    return "\n".join(lines)


@router.message(Command("status", "host", "machine"))
async def cmd_status(
    message: Message,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    updater: Optional[Updater] = None,
    heartbeat=None,
) -> None:
    await message.answer(await build_status(db, user, now, updater, heartbeat))
