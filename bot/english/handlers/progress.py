"""Прогресс: сколько слов, какая серия, что дальше."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ...db import Database, UserSettings
from .. import charts, content, quests
from ..db import streak
from ..keyboards import english_menu
from ..srs import LEARNED_BOX

router = Router(name="english-progress")
logger = logging.getLogger(__name__)

#: Грубая привязка выученного к уровням — чтобы прогресс был виден на глаз.
LEVELS = (
    (0, "самое начало"),
    (20, "первые слова"),
    (50, "A1 — понимаю простое"),
    (90, "A2 — держу простой диалог"),
    (130, "B1 — понимаю сюжет"),
    (170, "B1+ — сериал с английскими субтитрами"),
    (250, "B2 — смотрю без перевода"),
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


def next_level(learned: int) -> tuple[int, Optional[tuple[int, str]]]:
    """Порог текущего уровня и следующий уровень (порог, название), если он есть."""
    floor = LEVELS[0][0]
    for threshold, name in LEVELS:
        if learned >= threshold:
            floor = threshold
        else:
            return floor, (threshold, name)
    return floor, None


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
    mine = await db.eng_words(user.user_id)
    everything = len(content.CARDS) + len(mine)

    by_pack: dict[str, int] = {}
    for item in progress:
        if item.box >= LEARNED_BOX:
            if content.is_custom(item.item_id):
                by_pack[content.MY_PACK.key] = by_pack.get(content.MY_PACK.key, 0) + 1
                continue
            card = content.card_of(item.item_id)
            if card is not None:
                by_pack[card.pack] = by_pack.get(card.pack, 0) + 1

    packs = [
        (pack.title, by_pack.get(pack.key, 0), len(content.cards_of_pack(pack.key)))
        for pack in content.PACKS
    ]
    if mine:
        packs.append((content.MY_PACK.title, by_pack.get(content.MY_PACK.key, 0), len(mine)))

    markup = english_menu(due, quests.next_quest(done_quests) is not None)

    # Короткая подпись под картинкой: всё остальное уже нарисовано.
    caption = [f"📈 <b>Прогресс</b> · {level_of(learned)}"]
    if day.answered:
        caption.append(f"Сегодня: {day.answered} ответов, верных {day.correct}")
    caption.append(f"Повторить сегодня: {due}")
    caption.append(f"🗺 Квесты: {len(done_quests)} из {len(quests.QUESTS)}")
    if not mine:
        caption.append("<i>Свои слова: пришли «слово — перевод»</i>")

    image = await _picture(
        today, learned, total, row, packs, await db.eng_days_between(
            user.user_id, charts.calendar_start(today), today
        )
    )
    if image is not None:
        await message.answer_photo(
            BufferedInputFile(image, filename="english.png"),
            caption="\n".join(caption),
            reply_markup=markup,
        )
        return

    # без matplotlib — то же самое текстом
    lines = [
        "📈 <b>Английский: прогресс</b>",
        "",
        f"Выучено: <b>{learned}</b> из {everything}  {_bar(learned, everything)}",
        f"В работе: {total} · повторить сегодня: {due}",
        f"Уровень: <b>{level_of(learned)}</b>",
    ]
    if row:
        lines.append(f"🔥 Серия: <b>{row}</b> дней")
    if day.answered:
        lines.append(f"Сегодня: {day.answered} ответов, верных {day.correct}")
    lines.append("")
    lines.append("<b>По темам</b>")
    for title, known, size in packs:
        lines.append(f"{title}: {known}/{size} {_bar(known, size, 6)}")
    if not mine:
        lines.append(f"{content.MY_PACK.title}: пока нет — пришли «слово — перевод»")
    lines.append("")
    lines.append(f"🗺 Квесты: {len(done_quests)} из {len(quests.QUESTS)}")
    await message.answer("\n".join(lines), reply_markup=markup)


async def _picture(today, learned, in_work, row, packs, days) -> Optional[bytes]:
    if not charts.available():
        return None
    floor, upcoming = next_level(learned)
    snapshot = charts.Snapshot(
        today=today,
        days=days,
        learned=learned,
        in_work=in_work,
        streak=row,
        level=level_of(learned),
        next_level=upcoming,
        level_floor=floor,
        packs=packs,
    )
    try:
        # как и остальные графики — в том же потоке: pyplot не любит потоки
        return charts.progress_png(snapshot)
    except Exception:  # noqa: BLE001 - без картинки покажем текстом
        logger.exception("Не нарисовал прогресс по английскому")
        return None


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
