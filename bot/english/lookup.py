"""Слово из игры или фильма: прислал — перевёл — взял в изучение.

Это мостик между «встретил незнакомое слово» и «оно попало в повторения».
Работает только внутри раздела английского: в остальных «cost» — это скорее
опечатка в трате, чем запрос перевода.

Набор маленький, поэтому главное здесь — свои слова. «loot — добыча»
(можно с примером после точки с запятой) записывается в личный словарь и
приходит в карточках наравне с остальными. Учить то, что сам встретил в
игре или сериале, — самый живой способ: слово уже связано с моментом.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database, UserSettings
from . import content
from .srs import LEARNED_BOX

router = Router(name="english-lookup")

_LATIN = re.compile(r"^[a-zA-Z][a-zA-Z' \-]{0,30}$")


def looks_english(text: str) -> bool:
    return bool(_LATIN.match(text.strip()))


#: «loot — добыча», «loot = добыча», «loot: добыча», «loot - добыча».
#: Дефис только с пробелами вокруг — иначе «check-in» разрезало бы пополам.
_ENTRY = re.compile(
    r"^\s*(?P<en>[A-Za-z][A-Za-z' \-]{0,40}?)\s*(?:[—–=:]|\s-\s)\s*(?P<rest>.+)$",
    re.DOTALL,
)
_LETTER = re.compile(r"[^\W\d_]")

MAX_RU = 80
MAX_EXAMPLE = 200

ENTRY_HINT = (
    "Добавь своё: пришли <code>{word} — перевод</code>, и слово уйдёт в карточки.\n"
    "Можно с примером: <code>{word} — перевод; фраза, где встретил</code>"
)


def parse_entry(text: str) -> Optional[tuple[str, str, str]]:
    """«loot — добыча; We split the loot.» → ("loot", "добыча", "We split the loot.")."""
    match = _ENTRY.match(text)
    if not match:
        return None
    en = " ".join(match["en"].split()).strip(" -'")
    rest = match["rest"].strip()
    ru, _, example = _split_example(rest)
    if not en or not _LETTER.search(ru):
        return None  # «taxi — 300» — это трата, а не слово
    return en, ru[:MAX_RU], example[:MAX_EXAMPLE]


def _split_example(rest: str) -> tuple[str, str, str]:
    for separator in (";", "\n"):
        if separator in rest:
            ru, example = rest.split(separator, 1)
            return ru.strip(), separator, " ".join(example.split())
    return rest.strip(), "", ""


def find(text: str) -> list[content.Card]:
    """Точное совпадение, потом вхождение — «loot» найдёт и «loot box»."""
    needle = text.strip().lower()
    exact = [card for card in content.CARDS if card.en.lower() == needle]
    if exact:
        return exact
    return [card for card in content.CARDS if needle in card.en.lower()][:5]


def _card_text(card: content.Card) -> str:
    pack = content.PACK_BY_KEY.get(card.pack) or (
        content.MY_PACK if card.pack == content.MY_PACK.key else None
    )
    tail = f"\n\n<i>{pack.icon} {pack.title}</i>" if pack else ""
    lines = [f"🇬🇧 <b>{card.en}</b> — {card.ru}"]
    if card.example:
        lines.append("")
        lines.append(f"<i>{card.example}</i>")
    if card.example_ru:
        lines.append(f"<i>{card.example_ru}</i>")
    return "\n".join(lines) + tail


def _learn_button(card: content.Card) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 Взять в изучение", callback_data=f"eng:add:{card.id}")]
        ]
    )


def _saved_buttons(card: content.Card) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="▶️ Карточки", callback_data="eng:more"),
                InlineKeyboardButton(text="🗑 Убрать", callback_data=f"eng:del:{card.id}"),
            ]
        ]
    )


async def try_add_word(
    message: Message, text: str, db: Database, user: UserSettings, today: dt.date
) -> bool:
    """«слово — перевод» → в личный словарь. True — сообщение разобрано."""
    entry = parse_entry(text)
    if entry is None:
        return False
    en, ru, example = entry

    card, created = await db.eng_add_word(user.user_id, en, ru, example, today)
    word = f"<b>{card.en}</b> — {card.ru}"
    if created:
        head = f"📝 Записал в твой словарь: {word}"
        tail = "Придёт в ближайших карточках."
    else:
        head = f"📝 Обновил: {word}"
        tail = "Прогресс по слову сохранён."
    lines = [head]
    if card.example:
        lines.append(f"<i>{card.example}</i>")
    lines.append("")
    lines.append(tail)
    if not card.example:
        lines.append(
            "<i>Совет: фраза, где ты его встретил, запоминается лучше слова — "
            "её можно дописать после точки с запятой.</i>"
        )
    await message.answer("\n".join(lines), reply_markup=_saved_buttons(card))
    return True


async def try_lookup(
    message: Message,
    text: str,
    db: Database,
    user: UserSettings,
    today: Optional[dt.date] = None,
) -> bool:
    """True — сообщение разобрано и ответ отправлен."""
    if await try_add_word(message, text, db, user, today or dt.date.today()):
        return True
    if not looks_english(text):
        return False

    mine = await db.eng_find_word(user.user_id, text)
    if mine is not None:
        await message.answer(_card_text(mine), reply_markup=_saved_buttons(mine))
        return True

    found = find(text)
    word = html.escape(text.strip())
    if not found:
        await message.answer(
            f"🇬🇧 Слова <b>{word}</b> в моём наборе нет.\n\n"
            + ENTRY_HINT.format(word=word)
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


@router.callback_query(F.data.startswith("eng:del:"))
async def cb_delete(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    item_id = callback.data.split(":", 2)[2]
    if not await db.eng_delete_word(user.user_id, item_id):
        await callback.answer("Этого слова уже нет")
        return
    await callback.answer("Убрал")
    if isinstance(callback.message, Message):
        await callback.message.edit_text("🗑 Слово убрано из словаря.")


async def show_my_words(message: Message, db: Database, user: UserSettings) -> None:
    words = await db.eng_words(user.user_id)
    if not words:
        await message.answer(
            "📝 <b>Мои слова</b>\n\n"
            "Пока пусто. Встретил незнакомое слово в игре, фильме или песне — "
            "пришли его сюда так:\n"
            "<code>loot — добыча</code>\n"
            "или с фразой, где встретил:\n"
            "<code>loot — добыча; We split the loot.</code>\n\n"
            "Оно попадёт в карточки, и бот будет напоминать его, пока не запомнится."
        )
        return

    boxes = {item.item_id: item.box for item in await db.eng_progress(user.user_id)}
    learned = sum(1 for card in words if boxes.get(card.id, 0) >= LEARNED_BOX)
    lines = [f"📝 <b>Мои слова</b>: {len(words)}, выучено {learned}", ""]
    for card in words[:MY_WORDS_SHOWN]:
        mark = "✅" if boxes.get(card.id, 0) >= LEARNED_BOX else "·"
        lines.append(f"{mark} <b>{card.en}</b> — {card.ru}")
    if len(words) > MY_WORDS_SHOWN:
        lines.append(f"… и ещё {len(words) - MY_WORDS_SHOWN}")
    lines.append("")
    lines.append("<i>Убрать слово: пришли его — под ним будет кнопка «🗑 Убрать».</i>")
    await message.answer("\n".join(lines))


MY_WORDS_SHOWN = 40


@router.message(Command("mywords", "words"))
async def cmd_my_words(message: Message, db: Database, user: UserSettings) -> None:
    await show_my_words(message, db, user)


@router.callback_query(F.data == "eng:mine")
async def cb_my_words(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await show_my_words(callback.message, db, user)


@router.message(Command("word"))
async def cmd_word(
    message: Message, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Формат: <code>/word loot</code> — покажу перевод и пример.\n"
            "Своё слово: <code>/word loot — добыча</code>"
        )
        return
    if not await try_lookup(message, parts[1], db, user, now.date()):
        await message.answer("Это не похоже на английское слово.")
