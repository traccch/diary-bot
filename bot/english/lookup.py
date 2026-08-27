"""Слово из игры или фильма: прислал — перевёл — взял в изучение.

Это мостик между «встретил незнакомое слово» и «оно попало в повторения».
Работает только внутри раздела английского: в остальных «cost» — это скорее
опечатка в трате, чем запрос перевода.
"""

from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database, UserSettings
from . import content

router = Router(name="english-lookup")

_LATIN = re.compile(r"^[a-zA-Z][a-zA-Z' \-]{0,30}$")


def looks_english(text: str) -> bool:
    return bool(_LATIN.match(text.strip()))


def find(text: str) -> list[content.Card]:
    """Точное совпадение, потом вхождение — «loot» найдёт и «loot box»."""
    needle = text.strip().lower()
    exact = [card for card in content.CARDS if card.en.lower() == needle]
    if exact:
        return exact
    return [card for card in content.CARDS if needle in card.en.lower()][:5]


def _card_text(card: content.Card) -> str:
    pack = content.PACK_BY_KEY.get(card.pack)
    tail = f"\n\n<i>{pack.icon} {pack.title}</i>" if pack else ""
    return (
        f"🇬🇧 <b>{card.en}</b> — {card.ru}\n\n"
        f"<i>{card.example}</i>\n<i>{card.example_ru}</i>{tail}"
    )


def _learn_button(card: content.Card) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 Взять в изучение", callback_data=f"eng:add:{card.id}")]
        ]
    )


async def try_lookup(message: Message, text: str, db: Database, user: UserSettings) -> bool:
    """True — слово нашлось и ответ отправлен."""
    if not looks_english(text):
        return False

    found = find(text)
    if not found:
        await message.answer(
            f"🇬🇧 Слова <b>{text.strip()}</b> в моём наборе нет.\n\n"
            "Набор небольшой и заточен под игры, кино и песни — "
            "посмотреть, что в нём есть, можно в /engstats."
        )
        return True

    if len(found) == 1:
        await message.answer(_card_text(found[0]), reply_markup=_learn_button(found[0]))
        return True

    lines = ["🇬🇧 <b>Нашлось несколько</b>", ""]
    lines.extend(f"· <b>{card.en}</b> — {card.ru}" for card in found)
    await message.answer("\n".join(lines))
    return True


@router.callback_query(F.data.startswith("eng:add:"))
async def cb_add(
    callback: CallbackQuery, db: Database, user: UserSettings, now
) -> None:
    item_id = callback.data.split(":", 2)[2]
    card = content.card_of(item_id)
    if card is None:
        await callback.answer("Такого слова нет", show_alert=True)
        return

    existing = await db.eng_progress_of(user.user_id, item_id)
    if existing is not None:
        await callback.answer("Уже в изучении")
        return

    # box=0 и срок «сегодня» — слово попадёт в ближайшую сессию
    await db.eng_save_answer(
        user.user_id, item_id, 0, now.date(), correct=False, lapse=False
    )
    await callback.answer("Взял в изучение")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)


@router.message(Command("word"))
async def cmd_word(message: Message, db: Database, user: UserSettings) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Формат: <code>/word loot</code> — покажу перевод и пример."
        )
        return
    if not await try_lookup(message, parts[1], db, user):
        await message.answer("Это не похоже на английское слово.")
