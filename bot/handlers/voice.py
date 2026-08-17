"""Голосовые сообщения: наговорил измерение — бот его записал."""

from __future__ import annotations

import datetime as dt
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.types import Message

from ..ai import AiClient
from ..db import Database, UserSettings
from .ai_common import NO_AI, save_ai_entry

router = Router(name="voice")

#: Больше 10 МБ — это не «сказал давление», а что-то не то.
MAX_AUDIO_BYTES = 10 * 1024 * 1024

NOTHING_HEARD = (
    "🎧 Не разобрал в записи ни давления, ни показателей.\n"
    "Попробуй сказать проще: «сто двадцать на восемьдесят, пульс шестьдесят восемь»."
)


@router.message(F.voice | F.audio)
async def handle_voice(
    message: Message,
    bot: Bot,
    ai: AiClient,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
) -> None:
    if not ai.available():
        await message.answer(NO_AI)
        return

    source = message.voice or message.audio
    if source is None:
        return
    if (source.file_size or 0) > MAX_AUDIO_BYTES:
        await message.answer("Запись слишком длинная — уложись в пару минут.")
        return

    note = await message.answer("🎧 Слушаю…")

    buffer = BytesIO()
    await bot.download(source, destination=buffer)
    mime = getattr(source, "mime_type", None) or "audio/ogg"

    result = await ai.extract_from_audio(buffer.getvalue(), mime, now)
    if result is None:
        await note.edit_text(
            "🎧 Не получилось разобрать запись — сервис ИИ не ответил.\n"
            "Попробуй ещё раз или пришли цифрами: <code>120/80 68</code>."
        )
        return

    if not result:
        heard = f"\n\n<i>Услышал: {result.transcript}</i>" if result.transcript else ""
        await note.edit_text(NOTHING_HEARD + heard)
        return

    await note.edit_text(await save_ai_entry(db, user, now, result))
