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
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ...db import Database, UserSettings
from .. import content, quests
from ..keyboards import after_session, quest_next, quest_options, quest_start

router = Router(name="english-quest")


class EngQuest(StatesGroup):
    reading = State()
    answering = State()


ALL_DONE = (
    "🗺 <b>Квесты пройдены — все {count}</b>\n\n"
    "Дальше лучше всего работает настоящий материал: включи игру или серию "
    "с английскими субтитрами. Слова оттуда приноси сюда: "
    "<code>слово — перевод</code>, и оно уйдёт в карточки.\n\n"
    "А сцену можно пройти ещё раз — через неделю она читается совсем иначе."
)


def replay_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for quest in quests.QUESTS:
        builder.button(text=quest.label, callback_data=f"eq:open:{quest.id}")
    builder.adjust(2)
    return builder.as_markup()


def finish_actions(quest_id: str) -> InlineKeyboardMarkup:
    markup = after_session(True)
    learn = [
        InlineKeyboardButton(
            text="📚 Слова из сцены — в карточки", callback_data=f"eq:learn:{quest_id}"
        )
    ]
    return InlineKeyboardMarkup(inline_keyboard=[learn, *markup.inline_keyboard])


def _intro(quest: quests.Quest) -> str:
    vocab = "\n".join(f"· <b>{en}</b> — {ru}" for en, ru in quest.vocab)
    return (
        f"{quest.label}\n\n"
        f"<i>{quest.setting}</i>\n\n"
        f"<b>Слова, которые встретятся</b>\n{vocab}\n\n"
        f"{quest.scene_text}"
    )


async def open_quest(
    message: Message,
    db: Database,
    user: UserSettings,
    state: FSMContext,
    quest_id: str = "",
) -> None:
    if quest_id:
        quest = quests.quest_of(quest_id)
    else:
        quest = quests.next_quest(await db.eng_done_quests(user.user_id))
    if quest is None:
        await state.clear()
        await message.answer(
            ALL_DONE.format(count=len(quests.QUESTS)), reply_markup=replay_menu()
        )
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
        reply_markup=finish_actions(quest.id),
    )


async def learn_vocab(db: Database, user_id: int, quest: quests.Quest, today: dt.date) -> list[str]:
    """Слова сцены — в карточки. Возвращает те, что добавились впервые.

    Слово, которое есть в общем наборе, берётся оттуда (там пример и
    перевод выверены), остальные уходят в свой словарь с репликой из сцены
    как примером: так слово запомнится вместе с моментом.
    """
    added: list[str] = []
    for en, ru in quest.vocab:
        same = [card for card in content.CARDS if card.en.lower() == en.lower()]
        if same:
            if await db.eng_progress_of(user_id, same[0].id) is None:
                await db.eng_save_answer(user_id, same[0].id, 0, today, correct=False, lapse=False)
                added.append(en)
            continue
        _, created = await db.eng_add_word(user_id, en, ru, quests.line_with(quest, en), today)
        if created:
            added.append(en)
    return added


@router.callback_query(F.data.startswith("eq:learn:"))
async def cb_learn(
    callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    quest = quests.quest_of(callback.data.split(":", 2)[2])
    if quest is None:
        await callback.answer("Такого квеста нет")
        return
    added = await learn_vocab(db, user.user_id, quest, now.date())
    if not added:
        await callback.answer("Эти слова уже в карточках")
        return
    await callback.answer(f"Добавил: {len(added)}")
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "📚 В карточки ушло: " + ", ".join(f"<b>{en}</b>" for en in added)
            + "\n\nПридут в ближайшей сессии.",
            reply_markup=after_session(True),
        )


@router.callback_query(F.data.startswith("eq:open:"))
async def cb_open(
    callback: CallbackQuery, db: Database, user: UserSettings, state: FSMContext
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await open_quest(callback.message, db, user, state, callback.data.split(":", 2)[2])


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
