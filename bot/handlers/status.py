"""Состояние бота в чат: то же, что видно в консоли, но с телефона.

Окно с ботом стоит дома, а вопрос «чем он там занят» возникает как раз тогда,
когда до этого окна не дойти.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Optional

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

import os
import time

from .. import sysinfo
from ..db import Database, UserSettings
from ..formatting import esc
from ..reminders import next_fire, wait_text
from ..updater import Updater
from .update import RESTART_NOTICE

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


async def set_proxy(
    message: Message, wanted: str, db: Database, restart_event=None
) -> None:
    """Запоминает настройку прокси в базе и перезапускает бота."""
    from ..config import AUTO, read_proxy

    if wanted.lower() in {"off", "выкл", "нет", "напрямую", "0"}:
        await db.set_meta("proxy", "")
        value, said = "", "напрямую, без прокси"
    else:
        try:
            value = read_proxy(wanted)
        except RuntimeError as exc:
            await message.answer(f"⚠️ {esc(str(exc))}")
            return
        await db.set_meta("proxy", value)
        said = "искать локальный прокси" if value == AUTO else f"через {value}"

    if restart_event is None:
        await message.answer(
            f"Запомнил: {esc(said)}. Настройка вступит в силу после перезапуска."
        )
        return

    await message.answer(
        f"✅ Запомнил: <b>{esc(said)}</b>.\nПерезапускаюсь, чтобы применить."
    )
    await db.set_meta(RESTART_NOTICE, f"{message.chat.id}|{time.time():.0f}")
    restart_event.set()


def env_lines(env_file: str, lookalikes: tuple = ()) -> list[str]:
    """Что бот прочитал из настроек — и куда, возможно, ушли правки.

    «Не работает настройка» чаще всего означает не ошибку в настройке, а то,
    что правили другой файл. Пока не видно, какой файл прочитан, догадаться
    невозможно.
    """
    lines = []
    if env_file:
        raw = _read_setting(env_file, "TELEGRAM_PROXY")
        lines.append(f"Настройки читаю из <code>{esc(env_file)}</code>")
        lines.append(
            f"TELEGRAM_PROXY в нём: <b>{esc(raw)}</b>"
            if raw
            else "Строки <code>TELEGRAM_PROXY</code> в нём нет"
        )
    else:
        lines.append("Файла <code>.env</code> не нашёл вовсе")

    for path in lookalikes:
        lines.append(
            f"⚠️ Рядом лежит <code>{esc(os.path.basename(path))}</code> — "
            "настройки оттуда не читаются. Блокнот дописал расширение; "
            "переименуй файл в <code>.env</code>"
        )
    return lines


def _read_setting(path: str, key: str) -> str:
    try:
        with open(path, encoding="utf-8-sig") as handle:
            for line in handle:
                clean = line.strip()
                if clean.startswith(f"{key}="):
                    return clean.split("=", 1)[1].strip() or "(пусто)"
    except OSError:
        return ""
    return ""


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


@router.message(Command("proxy", "vpn"))
async def cmd_proxy(
    message: Message,
    command: Optional[CommandObject] = None,
    db: Optional[Database] = None,
    restart_event=None,
    proxy_now: str = "",
    env_file: str = "",
    env_lookalikes: tuple = (),
) -> None:
    """Проверка прокси — и его настройка, чтобы не лезть в файлы.

    Файл настроек лежит рядом с ботом, а человек с телефоном до него не
    дотянется; да и на компьютере легко открыть не тот файл. Поэтому
    «/proxy auto» делает то же самое, что строка в .env, только сразу.
    """
    from .. import proxyscan

    wanted = (command.args or "").strip() if command else ""
    if wanted and db is not None:
        await set_proxy(message, wanted, db, restart_event)
        return

    note = await message.answer("🔍 Смотрю, есть ли на компьютере локальный прокси…")
    probes = await proxyscan.scan()

    working = [item for item in probes if item.good]
    listening = [item for item in probes if item.open and not item.good]

    lines = ["🌐 <b>Прокси</b>", ""]
    lines.append(
        f"Бот сейчас ходит: <b>{esc(proxy_now)}</b>" if proxy_now
        else "Бот сейчас ходит: <b>напрямую</b>"
    )
    lines.extend(env_lines(env_file, env_lookalikes))
    lines.append("")

    if working:
        lines.append("Нашёл рабочий прокси:")
        lines.extend(f"· <code>{esc(item.url)}</code>" for item in working)
        if not proxy_now:
            lines.append("")
            lines.append(
                "<i>Впиши в .env строку <code>TELEGRAM_PROXY=auto</code> "
                "и перезапусти бота — он будет ходить через него.</i>"
            )
    elif listening:
        ports = ", ".join(str(item.port) for item in listening)
        lines.append(
            f"Порты {ports} кто-то слушает, но Telegram через них не открылся. "
            "Похоже, это не прокси или он не пускает наружу."
        )
    else:
        lines.append(
            "Локального прокси на этом компьютере нет: все известные порты "
            "закрыты."
        )
        lines.append("")
        lines.append(
            "<i>Обычно это значит, что VPN-приложение стоит только на телефоне "
            "или не запущено. Бот работает и напрямую — просто связь рвётся "
            "чаще.</i>"
        )

    await note.edit_text("\n".join(lines))


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
