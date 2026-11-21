"""Сессия карточек: несколько вопросов подряд, разбор после каждого.

Сессия живёт в состоянии диалога: очередь слов и текущий вопрос. Это
сознательно короткая история — три минуты, десять карточек. Если бот
перезапустится посреди сессии, потеряется только она, а весь прогресс уже
записан в базу после каждого ответа.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ...db import Database, UserSettings
from .. import content, quests, srs
from ..db import streak
from ..keyboards import after_card, after_session, answer_options, english_menu, type_answer
from .progress import level_of

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
    mine = len(await db.eng_words(user.user_id))

    lines = [
        "🇬🇧 <b>Английский</b>",
        "",
        f"Слов в работе: <b>{total}</b> · выучено: <b>{learned}</b>"
        f" из {len(content.CARDS) + mine}",
        f"Уровень: {level_of(learned)}",
    ]
    if due:
        lines.append(f"Пора повторить: <b>{due}</b>")
    if day.answered:
        lines.append(f"Сегодня уже: {day.answered} ответов, верных {day.correct}")
    lines.append("")
    lines.append("<i>Пять минут в день дают больше, чем час раз в неделю.</i>")
    lines.append(
        "<i>Встретил слово в игре — пришли «loot — добыча», оно уйдёт в карточки.</i>"
    )

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

    _, learned = await db.eng_counts(user.user_id)
    await state.set_state(EngSession.answering)
    await state.update_data(
        queue=queue, asked=0, correct=0, current=None, learned_before=learned
    )
    await _ask_next(message, db, user, state, today)


async def _ask_next(
    message: Message, db: Database, user: UserSettings, state: FSMContext, today: dt.date
) -> None:
    data = await state.get_data()
    queue: list[str] = list(data.get("queue") or [])
    if not queue:
        await _finish(message, db, user, state, today)
        return

    item_id = queue.pop(0)
    card = await db.eng_card(user.user_id, item_id)
    if card is None:  # карточку убрали из набора или из словаря — пропускаем
        await state.update_data(queue=queue)
        await _ask_next(message, db, user, state, today)
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
            "kind": question.kind,
        },
    )

    asked = int(data.get("asked") or 0) + 1
    total = asked + len(queue)
    await state.update_data(asked=asked)
    if question.kind == srs.TYPE:
        markup = type_answer()
        tail = "\n\n<i>Напечатай ответ сообщением.</i>"
    else:
        markup = answer_options(question.options)
        tail = ""
    await message.answer(
        f"<code>{progress_bar(asked - 1, total)}</code>  <b>{asked}/{total}</b>\n\n"
        f"{question.prompt}{tail}",
        reply_markup=markup,
    )


async def _record(
    db: Database,
    user: UserSettings,
    today: dt.date,
    state: FSMContext,
    known: bool,
    correct: bool,
    head: str = "",
) -> Optional[str]:
    """Записывает ответ на текущую карточку и возвращает разбор. None — карточки нет."""
    data = await state.get_data()
    current = data.get("current")
    if not current:
        return None
    # ответ принят — повторное нажатие или второе сообщение его не задвоят
    await state.update_data(current=None)

    item_id = current["item_id"]
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
    marks = list(data.get("marks") or []) + [MARK_RIGHT if correct else MARK_WRONG if known else MARK_SKIP]
    await state.update_data(marks=marks)
    if correct:
        await state.update_data(correct=int(data.get("correct") or 0) + 1)

    card = await db.eng_card(user.user_id, item_id)
    if not head:
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
        if card.example:
            body.append(f"<i>{card.example}</i>")
        if card.example_ru:
            body.append(f"<i>{card.example_ru}</i>")
    return "\n".join(body)


async def _grade(
    callback: CallbackQuery,
    db: Database,
    user: UserSettings,
    today: dt.date,
    state: FSMContext,
    chosen: int,
) -> None:
    current = (await state.get_data()).get("current")
    known = chosen >= 0
    correct = bool(current) and known and chosen == current["correct"]
    body = await _record(db, user, today, state, known, correct)
    await callback.answer()
    if body is not None and isinstance(callback.message, Message):
        await callback.message.edit_text(body, reply_markup=after_card())


async def grade_typed(
    message: Message, db: Database, user: UserSettings, today: dt.date, state: FSMContext
) -> bool:
    """Напечатанный ответ на карточку «напиши сам». False — сейчас такой не ждём."""
    current = (await state.get_data()).get("current")
    if not current or current.get("kind") != srs.TYPE:
        return False

    right = current["options"][0]
    verdict = srs.check_typed(right, message.text or "")
    if verdict == srs.EXACT:
        head = "✅ <b>Верно</b>"
    elif verdict == srs.TYPO:
        head = f"✅ <b>Засчитал</b> — с опечаткой, правильно: <b>{right}</b>"
    else:
        head = f"❌ <b>Нет</b> — правильно: <b>{right}</b>"
    body = await _record(db, user, today, state, True, verdict is not None, head)
    if body is not None:
        await message.answer(body, reply_markup=after_card())
    return True


async def _finish(
    message: Message, db: Database, user: UserSettings, state: FSMContext, today: dt.date
) -> None:
    data = await state.get_data()
    asked = int(data.get("asked") or 0)
    correct = int(data.get("correct") or 0)
    learned_before = data.get("learned_before")
    marks = "".join(data.get("marks") or [])
    await state.clear()

    days = await db.eng_active_days(user.user_id)
    row = streak(days, today)
    total, learned = await db.eng_counts(user.user_id)
    done = await db.eng_done_quests(user.user_id)

    lines = [
        f"🏁 <b>Сессия закончена</b> — {verdict(correct, asked)}",
        "",
    ]
    if marks:
        lines.append(marks)
    lines.extend(
        [
            f"Верных: <b>{correct}</b> из {asked}",
            f"Слов в работе: {total} · выучено: <b>{learned}</b>",
            "",
        ]
    )
    if row > 1:
        lines.append(f"🔥 Серия: <b>{row}</b> дней подряд")
    elif row == 1:
        lines.append("🔥 Серия началась — завтра не разрывай")
    lines.extend(milestones(learned_before, learned, row))

    await message.answer(
        "\n".join(lines), reply_markup=after_session(quests.next_quest(done) is not None)
    )


MARK_RIGHT = "🟩"
MARK_WRONG = "🟥"
MARK_SKIP = "⬜"


def progress_bar(done: int, total: int, width: int = 10) -> str:
    """Полоса «сколько уже пройдено» над вопросом."""
    if total <= 0:
        return "▱" * width
    filled = max(0, min(width, round(done / total * width)))
    return "▰" * filled + "▱" * (width - filled)


def verdict(correct: int, asked: int) -> str:
    if not asked:
        return "ничего не спросил"
    share = correct / asked
    if share >= 0.9:
        return "отлично 🎯"
    if share >= 0.7:
        return "хорошо 👍"
    if share >= 0.4:
        return "нормально, слова держатся"
    return "трудные слова — они вернутся завтра"


#: Серии, которые стоит отметить отдельно.
STREAK_MARKS = (3, 7, 14, 30, 50, 100, 200, 365)


def milestones(learned_before, learned: int, row: int) -> list[str]:
    """Что отпраздновать в конце сессии: выученные слова, новый уровень, серию."""
    lines: list[str] = []
    if isinstance(learned_before, int) and learned > learned_before:
        gained = learned - learned_before
        lines.append(f"🌱 Сегодня закрепилось: <b>+{gained}</b>")
        if level_of(learned) != level_of(learned_before):
            lines.append(f"🎉 Новый уровень: <b>{level_of(learned)}</b>")
    if row in STREAK_MARKS:
        lines.append(f"🏆 {row} дней подряд — это уже привычка")
    return lines


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
    callback: CallbackQuery,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    state: FSMContext,
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _ask_next(callback.message, db, user, state, now.date())


@router.message(EngSession.answering, F.text, ~F.text.startswith("/"))
async def on_text_mid_session(
    message: Message,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    state: FSMContext,
) -> None:
    """Посреди карточек прислали текст: ответ на «напиши сам» или слово из игры.

    Сессию закрываем (ответы уже записаны), а текст разбираем как обычное
    сообщение: слово переведётся или добавится, давление запишется.
    """
    from ...handlers.router import free_text_like  # там же подключён и этот роутер

    if await grade_typed(message, db, user, now.date(), state):
        return
    await state.clear()
    await free_text_like(message, message.text or "", db, user, now)


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
            "Сессия прервалась — эти кнопки уже не работают.\n"
            "Ответы, которые ты успел дать, засчитаны.",
            reply_markup=after_session(True),
        )
