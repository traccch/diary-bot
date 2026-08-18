"""Общие команды и свободный текст: что куда идёт.

Команда вроде /stats выполняется в текущем разделе. Свободный текст сначала
пробуют разобрать по правилам текущего раздела, а если не вышло — правилами
соседнего: «120/80» попадёт в дневник давления, даже если ты сейчас в деньгах,
а «кофе 300» — в расходы. Так переключаться приходится редко.
"""

from __future__ import annotations

import datetime as dt
import logging

import tempfile
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import Message, Voice

from .. import sections
from ..db import Database, UserSettings
from ..keyboards import section_menu
from ..money import handlers as money
from ..money.handlers.entry import HINT as MONEY_HINT
from ..money.handlers.entry import NOT_FOUND as MONEY_NOT_FOUND
from ..money.handlers.entry import save_transaction
from ..pressure import handlers as pressure
from ..pressure.handlers.entry import HINT as PRESSURE_HINT
from ..pressure.handlers.entry import NOT_FOUND as PRESSURE_NOT_FOUND
from ..pressure.handlers.entry import save_measurement
from ..pressure.parsing import looks_like_pressure
from ..voice import MAX_SECONDS, TranscribeError, Transcriber, clean_speech

router = Router(name="shared")
logger = logging.getLogger(__name__)

#: Команды, которые есть в обоих разделах и выполняются в текущем.
SHARED_COMMANDS = ("stats", "report", "last", "undo", "del", "export", "chart", "graph")

SECTION_MODULES = {sections.PRESSURE: pressure, sections.MONEY: money}


async def _save_in(
    key: str,
    message: Message,
    text: str,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    crossing: bool = False,
) -> str:
    """Пробует записать текст в раздел. `crossing` — попытка в соседний раздел."""
    if key == sections.PRESSURE:
        return await save_measurement(message, text, db, user, now)
    return await save_transaction(message, text, db, user, now.date(), crossing)


def _not_found(key: str) -> str:
    return PRESSURE_NOT_FOUND if key == sections.PRESSURE else MONEY_NOT_FOUND


@router.message(Command(*SHARED_COMMANDS))
async def dispatch_command(
    message: Message,
    command: CommandObject,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
) -> None:
    module = SECTION_MODULES.get(user.section, pressure)
    if await module.handle_command(command.command, message, command, db, user, now):
        return

    section = sections.section_of(user.section)
    await message.answer(
        f"В разделе {section.label} такой команды нет. Переключить раздел — /menu",
        reply_markup=section_menu(user.section),
    )


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def free_text(
    message: Message, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    await free_text_like(message, message.text or "", db, user, now)


@router.message(F.voice)
async def voice_message(
    message: Message,
    bot: Bot,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    transcriber: Transcriber,
) -> None:
    """Голосовое: расшифровываем и разбираем как обычный текст."""
    voice: Voice = message.voice
    if not transcriber.ready:
        reason = getattr(transcriber, "why_not_ready", lambda: "")()
        await message.answer(
            "🎤 Голосовые я пока не разбираю: " + (reason or "распознавание не настроено") +
            ".\nНапиши текстом — <code>120/80 68</code> или <code>кофе 300</code>."
        )
        return

    if voice.duration and voice.duration > MAX_SECONDS:
        await message.answer(
            f"🎤 Слишком длинная запись ({voice.duration} с). "
            f"Я разбираю до {MAX_SECONDS} секунд — скажи покороче."
        )
        return

    note = await message.answer("🎤 Слушаю…")
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "voice.oga"
        try:
            file = await bot.get_file(voice.file_id)
            await bot.download_file(file.file_path, destination=str(audio))
            text = clean_speech(await transcriber.transcribe(audio))
        except TranscribeError as exc:
            await message.answer(f"🎤 Не получилось: {exc}")
            return
        except Exception:  # noqa: BLE001 - голос не должен ронять диалог
            logger.exception("Сбой при расшифровке голосового")
            await message.answer("🎤 Не получилось расшифровать. Напиши текстом.")
            return

    logger.info("Расшифровал голосовое: %r", text)
    await note.edit_text(f"🎤 <i>Расслышал:</i> {text}")
    await free_text_like(message, text, db, user, now)


async def free_text_like(
    message: Message, text: str, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    """Разбор строки так же, как обычного сообщения — общий путь для голоса."""
    current = user.section if user.section in SECTION_MODULES else sections.DEFAULT
    other = sections.MONEY if current == sections.PRESSURE else sections.PRESSURE

    # «120/80» — это давление в любом разделе: денежный разбор увидел бы здесь
    # сумму 80 и записал трату
    if looks_like_pressure(text):
        current, other = sections.PRESSURE, sections.MONEY

    for key, crossing in ((current, False), (other, True)):
        if await _save_in(key, message, text, db, user, now, crossing) != _not_found(key):
            return

    hint = PRESSURE_HINT if current == sections.PRESSURE else MONEY_HINT
    await message.answer(hint, reply_markup=section_menu(current))
