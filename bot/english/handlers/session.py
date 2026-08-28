"""Сессия карточек: несколько вопросов подряд, разбор после каждого.

Сессия живёт в состоянии диалога: очередь слов и текущий вопрос. Это
сознательно короткая история — три минуты, десять карточек. Если бот
перезапустится посреди сессии, потеряется только она, а весь прогресс уже
записан в базу после каждого ответа.
"""

from __future__ import annotations

import datetime as dt
import random

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ...db import Database, UserSettings
from .. import content, quests, srs
from ..db import streak
from ..keyboards import after_card, after_session, answer_options, english_menu

router = Router(name="english-session")


class EngSession(StatesGroup):
    answering = State()


NOTHING_DUE = (
    "🇬🇧 <b>На сегодня всё</b>\n\n"
    "Повторять пока нечего, новые слова на сегодня тоже закончились — "
    "именно так и должно быть: интервальные повторения берут не объёмом, "
    "а регулярностью.\n\n"
    "Если хочется продолжить — пройди квест: там живая сцена целиком."
)


def _digits(value: int) -> str:
    return f"{value}"


async def open_menu(message: Message, db: Database, user: UserSettings, today: dt.date) -> None:
    """Экран раздела: сколько повторить, что открыто."""
    total, learned = await db.eng_counts(user.user_id)
    due = await db.eng_due_count(user.user_id, today)
    day = await db.eng_day(user.user_id, today)
    done = await db.eng_done_quests(user.user_id)
    has_quest = quests.next_quest(done) is not None

    lines = [
        "🇬🇧 <b>Английский</b>",
        "",
        f"Слов в работе: <b>{total}</b> · выучено: <b>{learned}</b> из {len(content.CARDS)}",
    ]
    if due:
        lines.append(f"Пора повторить: <b>{due}</b>")
    if day.answered:
        lines.append(f"Сегодня уже: {day.answered} ответов, верных {day.correct}")
    lines.append("")
    lines.append("<i>Пять минут в день дают больше, чем час раз в неделю.</i>")

    await message.answer("\n".join(lines), reply_markup=english_menu(due, has_quest))


async def start_session(
    message: Message,
    db: Database,
    user: UserSettings,
    today: dt.date,
    state: FSMContext,
) -> None:
    progress = await db.eng_progress(user.user_id)
    day = await db.eng_day(user.user_id, today)
    queue = srs.build_session(progress, today, new_today=day.new_seen)

    if not queue:
        done = await db.eng_done_quests(user.user_id)
        await state.clear()
        await message.answer(
            NOTHING_DUE, reply_markup=after_session(quests.next_quest(done) is not None)
        )
        return

    await state.set_state(EngSession.answering)
    await state.update_data(queue=queue, asked=0, correct=0, current=None)
    await _ask_next(message, db, user, state)


async def _ask_next(
    message: Message, db: Database, user: UserSettings, state: FSMContext
) -> None:
    data = await state.get_data()
    queue: list[str] = list(data.get("queue") or [])
    if not queue:
        await _finish(message, db, user, state)
        return

    item_id = queue.pop(0)
    card = content.card_of(item_id)
    if card is None:  # карточку убрали из набора — просто пропускаем
        await state.update_data(queue=queue)
        await _ask_next(message, db, user, state)
        return

    progress = await db.eng_progress_of(user.user_id, item_id)
    rng = random.Random()
    question = srs.make_question(card, srs.kind_for(progress, rng), rng)

    await state.update_data(
        queue=queue,
        current={
            "item_id": question.item_id,
            "options": list(question.options),
            "correct": question.correct,
            "hint": question.hint,
            "is_new": progress is None,
        },
    )

    asked = int(data.get("asked") or 0) + 1
    total = asked + len(queue)
    await state.update_data(asked=asked)
    await message.answer(
        f"<b>{asked}/{total}</b>  {question.prompt}",
        reply_markup=answer_options(question.options),
    )


async def _grade(
    callback: CallbackQuery,
    db: Database,
    user: UserSettings,
    today: dt.date,
    state: FSMContext,
    chosen: int,
) -> None:
    data = await state.get_data()
    current = data.get("current")
    if not current:
        await callback.answer()
        return

    item_id = current["item_id"]
    known = chosen >= 0
    correct = known and chosen == current["correct"]

    progress = await db.eng_progress_of(user.user_id, item_id)
    box = progress.box if progress else 0
    # «Не знаю» не считается ошибкой: карточка просто остаётся на месте.
    new_box = srs.next_box(box, correct) if known else box
    await db.eng_save_answer(
        user.user_id,
        item_id,
        new_box,
        srs.due_after(new_box, today) if known else today + dt.timedelta(days=1),
        correct=correct,
        lapse=known and not correct,
    )
    await db.eng_bump_day(user.user_id, today, correct, bool(current.get("is_new")))
    if correct:
        await state.update_data(correct=int(data.get("correct") or 0) + 1)

    card = content.card_of(item_id)
    if correct:
        head = "✅ <b>Верно</b>"
    elif known:
        right = current["options"][current["correct"]]
        head = f"❌ <b>Нет</b> — правильно: <b>{right}</b>"
    else:
        head = "🤷 <b>Ничего страшного</b>"

    body = [head, ""]
    if card is not None:
        body.append(f"<b>{card.en}</b> — {card.ru}")
        body.append(f"<i>{card.example}</i>")
        body.append(f"<i>{card.example_ru}</i>")

    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text("\n".join(body), reply_markup=after_card())


async def _finish(
    message: Message, db: Database, user: UserSettings, state: FSMContext
) -> None:
    data = await state.get_data()
    asked = int(data.get("asked") or 0)
    correct = int(data.get("correct") or 0)
    await state.clear()

    today = dt.date.today()
    days = await db.eng_active_days(user.user_id)
    row = streak(days, today)
    total, learned = await db.eng_counts(user.user_id)
    done = await db.eng_done_quests(user.user_id)

    lines = [
        "🏁 <b>Сессия закончена</b>",
        "",
        f"Ответов: <b>{asked}</b>, верных: <b>{correct}</b>",
        f"Слов в работе: {total} · выучено: {learned}",
    ]
    if row > 1:
        lines.append(f"🔥 Серия: <b>{row}</b> дней подряд")
    elif row == 1:
        lines.append("🔥 Серия началась — завтра не разрывай")

    await message.answer(
        "\n".join(lines), reply_markup=after_session(quests.next_quest(done) is not None)
    )


@router.message(Command("eng", "english"))
async def cmd_english(
    message: Message, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    await open_menu(message, db, user, now.date())


@router.callback_query(F.data == "eng:more")
async def cb_more(
    callback: CallbackQuery,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    state: FSMContext,
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await start_session(callback.message, db, user, now.date(), state)


@router.callback_query(EngSession.answering, F.data.startswith("eng:a:"))
async def cb_answer(
    callback: CallbackQuery,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    state: FSMContext,
) -> None:
    await _grade(callback, db, user, now.date(), state, int(callback.data.rsplit(":", 1)[1]))


@router.callback_query(EngSession.answering, F.data == "eng:idk")
async def cb_dont_know(
    callback: CallbackQuery,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    state: FSMContext,
) -> None:
    await _grade(callback, db, user, now.date(), state, -1)


@router.callback_query(EngSession.answering, F.data == "eng:next")
async def cb_next(
    callback: CallbackQuery, db: Database, user: UserSettings, state: FSMContext
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _ask_next(callback.message, db, user, state)


#: Последняя надежда: кнопка из сессии, которой больше нет. Так бывает, если
#: бот успел перезапуститься, а сообщение осталось висеть в чате. Молчать
#: нельзя — человек нажимает и не понимает, почему ничего не происходит.
#: Только кнопки самой сессии: «eng:add:…» из перевода слова живёт своей
#: жизнью и в этой ловушке не нуждается.
LOST_BUTTONS = ("eng:a:", "eng:idk", "eng:next")


@router.callback_query(
    F.data.func(lambda value: isinstance(value, str) and value.startswith(LOST_BUTTONS))
)
async def cb_lost_session(callback: CallbackQuery) -> None:
    await callback.answer("Эта сессия уже закрыта")
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Сессия прервалась — похоже, бот перезапускался.\n"
            "Ответы, которые ты успел дать, засчитаны.",
            reply_markup=after_session(True),
        )
