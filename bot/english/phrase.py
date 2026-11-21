"""Фраза дня: одна живая фраза в день, пять секунд на чтение.

Карточки требуют сесть и позаниматься, а фраза дня просто приходит: в
напоминании или по кнопке. Берётся из разговорки и кино — того, что реально
говорят. Сначала то, чего в карточках ещё нет; за день фраза не меняется,
даже если открыть её несколько раз.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Optional, Sequence

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database, UserSettings
from . import content

router = Router(name="english-phrase")

#: Откуда брать фразы: живая речь, а не словарь.
PACKS = ("talk", "films")


def meta_key(user_id: int) -> str:
    return f"eng_phrase:{user_id}"


def pick(day: dt.date, known: Sequence[str], user_id: int = 0) -> content.Card:
    """Фраза на день: из ещё не взятых в изучение, а если все взяты — из любых."""
    pool = [card for card in content.CARDS if card.pack in PACKS]
    taken = set(known)
    fresh = [card for card in pool if card.id not in taken] or pool
    return random.Random(day.toordinal() * 7919 + user_id).choice(fresh)


async def phrase_of_day(db: Database, user_id: int, day: dt.date) -> tuple[content.Card, bool]:
    """Фраза на сегодня и True, если сегодня её ещё не показывали.

    Выбор запоминается: иначе взял фразу в карточки — и «фраза дня» тут же
    сменилась бы на другую.
    """
    stored = await db.get_meta(meta_key(user_id)) or ""
    on, _, item_id = stored.partition("|")
    if on == day.isoformat():
        card = content.card_of(item_id)
        if card is not None:
            return card, False

    known = [item.item_id for item in await db.eng_progress(user_id)]
    card = pick(day, known, user_id)
    await db.set_meta(meta_key(user_id), f"{day.isoformat()}|{card.id}")
    return card, True


def render(card: content.Card, heading: str = "💬 <b>Фраза дня</b>") -> str:
    return (
        f"{heading}\n\n"
        f"<b>{card.en}</b> — {card.ru}\n\n"
        f"<i>{card.example}</i>\n<i>{card.example_ru}</i>"
    )


def phrase_actions(card: content.Card, with_cards: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="📚 В карточки", callback_data=f"eng:add:{card.id}"),
            InlineKeyboardButton(text="🎙 Сказать вслух", callback_data=f"eng:say:{card.id}"),
        ]
    ]
    if with_cards:
        rows.insert(
            0,
            [
                InlineKeyboardButton(text="▶️ Карточки", callback_data="eng:more"),
                InlineKeyboardButton(
                    text="⏱ Через 15 минут", callback_data="rem:snooze:english"
                ),
            ],
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def reminder_text(db: Database, user_id: int, day: dt.date, base: str) -> tuple[str, Optional[InlineKeyboardMarkup]]:
    """Текст напоминания про английский: с фразой дня, если сегодня её ещё не было."""
    card, first = await phrase_of_day(db, user_id, day)
    if not first:
        return base, None
    return f"{base}\n\n{render(card, '💬 <b>А пока — фраза дня</b>')}", phrase_actions(card, True)


async def show_phrase(message: Message, db: Database, user: UserSettings, day: dt.date) -> None:
    card, _ = await phrase_of_day(db, user.user_id, day)
    await message.answer(render(card), reply_markup=phrase_actions(card))


@router.message(Command("phrase"))
async def cmd_phrase(message: Message, db: Database, user: UserSettings, now: dt.datetime) -> None:
    await show_phrase(message, db, user, now.date())


@router.callback_query(F.data == "eng:phrase")
async def cb_phrase(
    callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await show_phrase(callback.message, db, user, now.date())
