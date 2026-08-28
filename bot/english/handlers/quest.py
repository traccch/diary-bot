"""Квесты: сцена целиком и вопросы по смыслу.

Отличие от карточек — здесь не переводят слова, а понимают связный кусок.
Сначала бот даёт четыре слова из сцены, потом саму сцену, потом три вопроса.
"""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ...db import Database, UserSettings
from .. import quests
from ..keyboards import after_session, quest_next, quest_options, quest_start

router = Router(name="english-quest")


class EngQuest(StatesGroup):
    reading = State()
    answering = State()


ALL_DONE = (
    "🗺 <b>Квесты пройдены — все пять</b>\n\n"
    "Дальше лучше всего работает настоящий материал: включи игру или серию "
    "с английскими субтитрами. Слова оттуда можно приносить сюда — просто "
    "пришли слово, и я покажу, что оно значит."
)


def _intro(quest: quests.Quest) -> str:
    vocab = "\n".join(f"· <b>{en}</b> — {ru}" for en, ru in quest.vocab)
    return (
        f"{quest.label}\n\n"
        f"<i>{quest.setting}</i>\n\n"
        f"<b>Слова, которые встретятся</b>\n{vocab}\n\n"
        f"{quest.scene_text}"
    )


async def open_quest(
    message: Message, db: Database, user: UserSettings, state: FSMContext
) -> None:
    done = await db.eng_done_quests(user.user_id)
    quest = quests.next_quest(done)
    if quest is None:
        await state.clear()
        await message.answer(ALL_DONE, reply_markup=after_session(False))
        return

    await state.set_state(EngQuest.reading)
    await state.update_data(quest_id=quest.id, index=0, score=0)
    await message.answer(_intro(quest), reply_markup=quest_start(quest.id))


async def _ask(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    quest = quests.quest_of(str(data.get("quest_id") or ""))
    index = int(data.get("index") or 0)
    if quest is None:
        await state.clear()
        return

    if index >= len(quest.questions):
        await _finish(message, state)
        return

    question = quest.questions[index]
    await state.set_state(EngQuest.answering)
    await message.answer(
        f"<b>Вопрос {index + 1} из {len(quest.questions)}</b>\n\n{question.text}",
        reply_markup=quest_options(question.options),
    )


async def _finish(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    quest = quests.quest_of(str(data.get("quest_id") or ""))
    score = int(data.get("score") or 0)
    await state.clear()
    if quest is None:
        return

    total = len(quest.questions)
    if score == total:
        head = "🏆 <b>Всё верно</b>"
    elif score >= total - 1:
        head = "👍 <b>Почти всё</b>"
    else:
        head = "🙂 <b>Есть что перечитать</b>"

    await message.answer(
        f"{head}\n\nКвест «{quest.title}»: {score} из {total}.\n\n"
        "<i>Перечитай сцену ещё раз — теперь она читается иначе.</i>",
        reply_markup=after_session(True),
    )


@router.message(Command("quest"))
async def cmd_quest(
    message: Message, db: Database, user: UserSettings, state: FSMContext
) -> None:
    await open_quest(message, db, user, state)


@router.callback_query(F.data == "eng:quest")
async def cb_quest(
    callback: CallbackQuery, db: Database, user: UserSettings, state: FSMContext
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await open_quest(callback.message, db, user, state)


@router.callback_query(EngQuest.reading, F.data.startswith("eq:go:"))
async def cb_go(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _ask(callback.message, state)


@router.callback_query(EngQuest.answering, F.data.startswith("eq:a:"))
async def cb_answer(
    callback: CallbackQuery,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    quest = quests.quest_of(str(data.get("quest_id") or ""))
    index = int(data.get("index") or 0)
    if quest is None or index >= len(quest.questions):
        await callback.answer()
        return

    question = quest.questions[index]
    chosen = int(callback.data.rsplit(":", 1)[1])
    correct = chosen == question.correct
    score = int(data.get("score") or 0) + int(correct)

    await state.update_data(index=index + 1, score=score)
    if index + 1 >= len(quest.questions):
        await db.eng_finish_quest(user.user_id, quest.id, now.date(), score)

    head = "✅ <b>Верно</b>" if correct else (
        f"❌ <b>Нет</b> — правильно: <b>{question.options[question.correct]}</b>"
    )
    body = [head]
    if question.explain:
        body.append("")
        body.append(f"<i>{question.explain}</i>")

    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text("\n".join(body), reply_markup=quest_next())


@router.callback_query(EngQuest.answering, F.data == "eq:next")
async def cb_next(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _ask(callback.message, state)


@router.callback_query(F.data.startswith("eq:"))
async def cb_lost_quest(callback: CallbackQuery) -> None:
    """Кнопка из квеста, которого больше нет: бот перезапускался."""
    await callback.answer("Этот квест уже закрыт")
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Квест прервался — похоже, бот перезапускался. Начать заново — /quest"
        )
