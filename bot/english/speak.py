"""Говорение: бот показывает фразу, человек говорит её голосовым, бот сверяет.

Понимать на слух и говорить — разные навыки, и второй карточками не
натренировать. Здесь распознавание речи работает судьёй: если whisper
разобрал фразу, носитель тоже разберёт. Это не оценка акцента, а проверка
«меня понятно».

Фраза берётся из того, что уже учишь (пример из карточки), а если учить
пока нечего — из разговорки.
"""

from __future__ import annotations

import datetime as dt
import difflib
import html
import random
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database, UserSettings
from ..voice import Transcriber
from . import content

router = Router(name="english-speak")


class EngSpeak(StatesGroup):
    waiting = State()


_WORD = re.compile(r"[a-z0-9']+")


def words(text: str) -> list[str]:
    text = html.unescape(text).lower().replace("’", "'")
    return _WORD.findall(text)


@dataclass(frozen=True)
class Verdict:
    score: float  # доля слов фразы, которые расслышаны
    marked: str  # фраза, где нерасслышанные слова подчёркнуты


def compare(target: str, heard: str) -> Verdict:
    """Сверяет сказанное с фразой по словам, порядок важен."""
    want = words(target)
    got = words(heard)
    if not want:
        return Verdict(1.0, html.escape(target, quote=False))

    matched: set[int] = set()
    for block in difflib.SequenceMatcher(None, want, got, autojunk=False).get_matching_blocks():
        matched.update(range(block.a, block.a + block.size))

    # подчёркиваем в исходной фразе, сохраняя её знаки препинания
    tokens = re.split(r"([A-Za-z0-9'’]+)", html.unescape(target))
    out: list[str] = []
    index = 0
    for token in tokens:
        if _WORD.fullmatch(token.lower().replace("’", "'")):
            safe = html.escape(token, quote=False)
            out.append(safe if index in matched else f"<u>{safe}</u>")
            index += 1
        else:
            out.append(html.escape(token, quote=False))
    return Verdict(len(matched) / len(want), "".join(out))


def choose(progress_ids: Sequence[str], rng: Optional[random.Random] = None) -> content.Card:
    """Пример из карточки, которую уже учишь; иначе — разговорка."""
    rng = rng or random.Random()
    studied = [
        card
        for card in (content.card_of(item_id) for item_id in progress_ids)
        if card is not None and card.example
    ]
    pool = studied or content.cards_of_pack("talk")
    return rng.choice(pool)


def _actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="▶️ Другая фраза", callback_data="eng:say"),
                InlineKeyboardButton(text="✖️ Хватит", callback_data="eng:sayoff"),
            ]
        ]
    )


async def offer(
    message: Message,
    db: Database,
    user: UserSettings,
    state: FSMContext,
    card: Optional[content.Card] = None,
) -> None:
    if card is None:
        progress = await db.eng_progress(user.user_id)
        card = choose([item.item_id for item in progress if item.seen])
    await state.set_state(EngSpeak.waiting)
    await state.update_data(target=card.example, item_id=card.id, tries=0)
    await message.answer(
        "🎙 <b>Скажи вслух</b> — запиши голосовое:\n\n"
        f"<b>{card.example}</b>\n<i>{card.example_ru}</i>\n\n"
        "<i>Говори спокойно, как в разговоре. Я сверю, что расслышал.</i>",
        reply_markup=_actions(),
    )


def _feedback(verdict: Verdict, heard: str) -> str:
    if verdict.score >= 0.99:
        head = "🎯 <b>Чисто</b> — понятно с первого раза."
    elif verdict.score >= 0.7:
        head = "👍 <b>Почти</b> — подчёркнутые слова я не расслышал."
    else:
        head = "🔁 <b>Пока не разобрал</b> — попробуй медленнее, подчёркнутое не услышал."
    return (
        f"{head}\n\n{verdict.marked}\n\n"
        f"<i>Расслышал:</i> {html.escape(heard) or '…'}\n\n"
        "Можно записать ещё раз или взять другую фразу."
    )


@router.message(Command("say", "speak"))
async def cmd_say(message: Message, db: Database, user: UserSettings, state: FSMContext) -> None:
    await offer(message, db, user, state)


@router.callback_query(F.data.startswith("eng:say"))
async def cb_say(
    callback: CallbackQuery, db: Database, user: UserSettings, state: FSMContext
) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    if callback.data == "eng:sayoff":
        await state.clear()
        await callback.message.answer("Ок, хватит на сейчас. Вернуться — /say")
        return
    card = None
    if callback.data.startswith("eng:say:"):
        card = content.card_of(callback.data.split(":", 2)[2])
    await offer(callback.message, db, user, state, card)


@router.message(EngSpeak.waiting, F.voice)
async def on_voice(
    message: Message,
    bot: Bot,
    state: FSMContext,
    transcriber: Transcriber,
) -> None:
    from ..handlers.router import hear  # там же подключён и этот роутер

    if not transcriber.ready:
        reason = getattr(transcriber, "why_not_ready", lambda: "")()
        await message.answer(
            "🎤 Голосовые я пока не разбираю: " + (reason or "распознавание не настроено")
            + ".\nКак включить — в README, раздел «Голосовой ввод»."
        )
        return

    data = await state.get_data()
    target = str(data.get("target") or "")
    note = await message.answer("🎤 Слушаю…")
    heard = await hear(message, bot, transcriber, language="en")
    if heard is None:
        return
    await state.update_data(tries=int(data.get("tries") or 0) + 1)
    await note.edit_text(_feedback(compare(target, heard), heard.strip()), reply_markup=_actions())


@router.message(EngSpeak.waiting, F.text, ~F.text.startswith("/"))
async def on_text(
    message: Message, db: Database, user: UserSettings, now: dt.datetime, state: FSMContext
) -> None:
    """Вместо голосового прислали текст — выходим из режима и разбираем как обычно."""
    from ..handlers.router import free_text_like

    await state.clear()
    await free_text_like(message, message.text or "", db, user, now)
