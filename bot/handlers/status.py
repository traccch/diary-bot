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
from aiogram.types import BufferedInputFile, Message

import os

from .. import sysinfo
from ..db import Database, UserSettings
from ..formatting import esc
from ..reminders import next_fire, wait_text
from ..updater import Updater

router = Router(name="status")

#: Сколько хвоста журнала отдавать файлом.
LOG_TAIL_BYTES = 400 * 1024

#: Меньше этого свободного места — пора беспокоиться: обновление тянет
#: зависимости, git держит копию, а база растёт молча.
LOW_DISK_MB = 3 * 1024

#: Между двумя замерами загрузки должно пройти хоть немного времени.
SAMPLE_SECONDS = 0.6

#: Где журнал лежит по умолчанию — тот же путь, что в настройках.
DEFAULT_LOG = os.path.join("data", "bot.log")


def disk_warning() -> str:
    """Предупреждение о свободном месте — молча кончившийся диск страшнее.

    Когда места нет, SQLite не может дописать журнал, и запись просто не
    сохраняется. Лучше сказать заранее.
    """
    free = sysinfo.disk_free_mb()
    if free is None or free >= LOW_DISK_MB:
        return ""
    return (
        f"⚠️ <b>На диске мало места: {free / 1024:.1f} ГБ</b>. "
        "Обновление тянет зависимости, база растёт — стоит освободить."
    ).replace(".", ",", 1)


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

    warning = disk_warning()
    if warning:
        lines.append(warning)

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


@router.message(Command("log", "logs"))
async def cmd_log(message: Message, config_log_path: str = "") -> None:
    """Отдаёт хвост журнала файлом — чтобы его не пересказывали по памяти."""
    path = config_log_path or DEFAULT_LOG
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > LOG_TAIL_BYTES:
                handle.seek(size - LOG_TAIL_BYTES)
                handle.readline()  # обрезанную строку не отдаём
            tail = handle.read()
    except OSError:
        await message.answer(
            "Журнал пока не ведётся. Он появится в <code>data/bot.log</code> "
            "после следующего запуска бота."
        )
        return

    if not tail.strip():
        await message.answer("Журнал пуст — писать было нечего.")
        return

    stamp = dt.datetime.now().strftime("%d.%m-%H%M")
    await message.answer_document(
        BufferedInputFile(tail, filename=f"diary-{stamp}.log"),
        caption=(
            f"📄 Журнал бота · {len(tail) / 1024:.0f} КБ\n"
            "<i>Последние события: запуск, сообщения, обрывы связи, обновления.</i>"
        ),
    )


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
