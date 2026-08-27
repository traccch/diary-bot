"""Прогресс: сколько слов, какая серия, что дальше."""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ...db import Database, UserSettings
from .. import content, quests
from ..db import streak
from ..keyboards import english_menu
from ..srs import LEARNED_BOX

router = Router(name="english-progress")

#: Грубая привязка выученного к уровням — чтобы прогресс был виден на глаз.
LEVELS = (
    (0, "самое начало"),
    (20, "первые слова"),
    (50, "A1 — понимаю простое"),
    (90, "A2 — держу простой диалог"),
    (130, "B1 — понимаю сюжет"),
)


def level_of(learned: int) -> str:
    title = LEVELS[0][1]
    for threshold, name in LEVELS:
        if learned >= threshold:
            title = name
    return title


def _bar(done: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "░" * width
    filled = max(0, min(width, round(done / total * width)))
    return "█" * filled + "░" * (width - filled)


async def show_progress(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    total, learned = await db.eng_counts(user.user_id)
    due = await db.eng_due_count(user.user_id, today)
    day = await db.eng_day(user.user_id, today)
    days = await db.eng_active_days(user.user_id)
    row = streak(days, today)
    done_quests = await db.eng_done_quests(user.user_id)
    progress = await db.eng_progress(user.user_id)

    lines = [
        "📈 <b>Английский: прогресс</b>",
        "",
        f"Выучено: <b>{learned}</b> из {len(content.CARDS)}  {_bar(learned, len(content.CARDS))}",
        f"В работе: {total} · повторить сегодня: {due}",
        f"Уровень: <b>{level_of(learned)}</b>",
    ]
    if row:
        lines.append(f"🔥 Серия: <b>{row}</b> дней")
    if day.answered:
        lines.append(f"Сегодня: {day.answered} ответов, верных {day.correct}")

    by_pack: dict[str, int] = {}
    for item in progress:
        if item.box >= LEARNED_BOX:
            card = content.card_of(item.item_id)
            if card is not None:
                by_pack[card.pack] = by_pack.get(card.pack, 0) + 1

    lines.append("")
    lines.append("<b>По темам</b>")
    for pack in content.PACKS:
        total_in_pack = len(content.cards_of_pack(pack.key))
        known = by_pack.get(pack.key, 0)
        lines.append(
            f"{pack.icon} {pack.title}: {known}/{total_in_pack} "
            f"{_bar(known, total_in_pack, 6)}"
        )

    lines.append("")
    lines.append(f"🗺 Квесты: {len(done_quests)} из {len(quests.QUESTS)}")

    await message.answer(
        "\n".join(lines),
        reply_markup=english_menu(due, quests.next_quest(done_quests) is not None),
    )


@router.message(Command("engstats", "progress"))
async def cmd_progress(
    message: Message, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    await show_progress(message, db, user, now.date())


@router.callback_query(F.data == "eng:stats")
async def cb_progress(
    callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await show_progress(callback.message, db, user, now.date())
