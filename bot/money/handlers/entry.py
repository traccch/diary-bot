"""Ввод операций: свободный текст, список, удаление, смена категории, выгрузка."""

from __future__ import annotations

import csv
import datetime as dt
import io

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ...db import Database, UserSettings
from ...formatting import esc, format_money
from ..db import EXPENSE, INCOME, Transaction
from ..formatting import render_line, render_transaction
from ..keyboards import category_picker, delete_buttons, transaction_actions
from ..parsing import ParseError, first_keyword, match_category, parse_transaction
from ..stats import check_limits, records_word

router = Router(name="money-entry")

#: Чем закончилась попытка записать операцию.
SAVED = "saved"
INVALID = "invalid"
NOT_FOUND = "not_found"

HINT = (
    "Не нашёл сумму. Напиши так:\n"
    "· <code>кофе 300</code> — расход\n"
    "· <code>450 такси вчера</code> — с датой\n"
    "· <code>аренда 45к</code> — тысячи сокращённо\n"
    "· <code>+90000 зарплата</code> — доход, через плюс\n\n"
    "Все команды — /help"
)


class Manual(StatesGroup):
    """Запись через кнопку: бот спросил — человек ответил одной строкой."""

    waiting_expense = State()
    waiting_income = State()


ASK_EXPENSE = (
    "💸 <b>Что потратил?</b>\n"
    "Одной строкой: <code>кофе 300</code>, <code>такси 450 вчера</code>, "
    "<code>аренда 45к</code>."
)
ASK_INCOME = (
    "💵 <b>Что пришло?</b>\n"
    "Одной строкой: <code>зарплата 90000</code>, <code>вернули за билет 5000</code>."
)


async def ask_expense(message: Message, state: FSMContext) -> None:
    await state.set_state(Manual.waiting_expense)
    await message.answer(ASK_EXPENSE + "\n\n<i>Передумал — /cancel</i>")


async def ask_income(message: Message, state: FSMContext) -> None:
    await state.set_state(Manual.waiting_income)
    await message.answer(ASK_INCOME + "\n\n<i>Передумал — /cancel</i>")


@router.message(Command("cancel"), Manual.waiting_expense)
@router.message(Command("cancel"), Manual.waiting_income)
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменил. Записать можно и просто текстом: <code>кофе 300</code>")


@router.message(Manual.waiting_expense, F.text, ~F.text.startswith("/"))
async def state_expense(
    message: Message, state: FSMContext, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    result = await save_transaction(message, message.text or "", db, user, now.date())
    if result == SAVED:
        await state.clear()
    elif result == NOT_FOUND:
        await message.answer(
            "Пока не вижу суммы. Нужно число, например <code>кофе 300</code>.\n"
            "<i>Выйти — /cancel</i>"
        )


@router.message(Manual.waiting_income, F.text, ~F.text.startswith("/"))
async def state_income(
    message: Message, state: FSMContext, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    # в этом состоянии всё — доход, поэтому плюс дописываем сами
    text = (message.text or "").strip()
    result = await save_transaction(
        message, text if text.startswith("+") else f"+{text}", db, user, now.date()
    )
    if result == SAVED:
        await state.clear()
    elif result == NOT_FOUND:
        await message.answer(
            "Пока не вижу суммы. Нужно число, например <code>зарплата 90000</code>.\n"
            "<i>Выйти — /cancel</i>"
        )


async def save_transaction(
    message: Message,
    text: str,
    db: Database,
    user: UserSettings,
    today: dt.date,
    require_context: bool = False,
) -> str:
    """Разбирает текст и сохраняет операцию. SAVED / INVALID / NOT_FOUND.

    `require_context` включается, когда строку принесли из другого раздела:
    там голое число — это, скорее всего, не трата, а недописанное давление,
    и записывать его рублями не стоит. Нужен либо комментарий, либо знак.
    """
    try:
        parsed = parse_transaction(text, today)
    except ParseError as exc:
        if require_context:
            return NOT_FOUND
        await message.answer(f"⚠️ {exc}")
        return INVALID

    if parsed is None:
        return NOT_FOUND

    if require_context and not parsed.note and not text.strip().startswith(("+", "-")):
        return NOT_FOUND

    categories = await db.list_categories(user.user_id, parsed.kind)
    category = match_category(parsed.note, categories)
    if category is None:
        category = await db.get_fallback_category(user.user_id, parsed.kind)

    transaction = await db.add_transaction(
        user_id=user.user_id,
        kind=parsed.kind,
        amount=parsed.amount,
        note=parsed.note,
        happened_on=parsed.happened_on,
        category_id=category.id if category else None,
    )

    litres = await _remember_fuel(db, user, transaction, text, parsed.happened_on)

    blocks = [render_transaction(transaction, user.currency, today)]
    if litres:
        blocks.append(
            f"⛽ Записал заправку: <b>{litres:.1f} л</b>".replace(".", ",")
            + f" · {format_money(round(parsed.amount / litres), user.currency)} за литр"
        )
    if not transaction.is_income:
        warnings = await check_limits(db, user, transaction.category_id, today)
        if warnings:
            blocks.append("\n".join(warnings))

    await message.answer(
        "\n\n".join(blocks), reply_markup=transaction_actions(transaction)
    )
    return SAVED


async def _remember_fuel(
    db: Database, user: UserSettings, transaction, text: str, on_date: dt.date
):
    """Заправка — это не только трата: литры нужны, чтобы считать расход."""
    from ...car.parsing import looks_like_fuel, parse_litres

    if transaction.is_income:
        return None
    litres = parse_litres(text)
    if litres is None or not looks_like_fuel(text):
        return None

    await db.add_fuel(user.user_id, on_date, litres, transaction.amount, transaction.note)
    return litres


async def cmd_last(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    transactions = await db.last_transactions(user.user_id, limit=10)
    if not transactions:
        await message.answer("Записей пока нет. Напиши, например: <code>кофе 300</code>")
        return

    lines = [f"🧾 <b>Последние {len(transactions)} {records_word(len(transactions))}</b>", ""]
    lines.extend(
        render_line(transaction, user.currency, today) for transaction in transactions
    )
    await message.answer("\n".join(lines), reply_markup=delete_buttons(transactions))


async def cmd_undo(message: Message, db: Database, user: UserSettings) -> None:
    recent = await db.last_transactions(user.user_id, limit=1)
    if not recent:
        await message.answer("Удалять нечего — записей нет.")
        return
    transaction = recent[0]
    await db.delete_transaction(user.user_id, transaction.id)
    await message.answer(
        f"🗑 Удалил: {format_money(transaction.amount, user.currency)} — "
        f"{esc(transaction.category_title)}"
    )


async def cmd_del(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    raw = (command.args or "").strip().lstrip("#")
    if not raw.isdigit():
        await message.answer("Укажи номер: <code>/del 42</code> (номера есть в /last).")
        return
    if await db.delete_transaction(user.user_id, int(raw)):
        await message.answer(f"🗑 Запись #{int(raw)} удалена.")
    else:
        await message.answer("Такой записи нет.")


async def cmd_export(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    first = await db.first_transaction_date(user.user_id)
    if first is None:
        await message.answer("Экспортировать нечего — записей нет.")
        return

    transactions = await db.transactions_between(user.user_id, first, dt.date(2999, 12, 31))
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["дата", "вид", "сумма", "валюта", "категория", "комментарий"])
    for transaction in transactions:
        writer.writerow(
            [
                transaction.happened_on.isoformat(),
                "доход" if transaction.is_income else "расход",
                f"{transaction.amount / 100:.2f}".replace(".", ","),
                user.currency,
                transaction.category_name,
                transaction.note,
            ]
        )

    payload = buffer.getvalue().encode("utf-8-sig")  # BOM, чтобы Excel не ломал кириллицу
    await message.answer_document(
        BufferedInputFile(payload, filename=f"money-{today.isoformat()}.csv"),
        caption=f"Выгрузил {len(transactions)} {records_word(len(transactions))}.",
    )


# ------------------------------------------------------------------ кнопки


@router.callback_query(F.data.startswith("delop:"))
async def cb_delete(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    transaction_id = int(callback.data.split(":", 1)[1])
    deleted = await db.delete_transaction(user.user_id, transaction_id)
    await callback.answer("Удалено" if deleted else "Уже удалено")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"🗑 Запись #{transaction_id} удалена.", reply_markup=None
        )


@router.callback_query(F.data.startswith("pickcat:"))
async def cb_pick_category(
    callback: CallbackQuery, db: Database, user: UserSettings
) -> None:
    transaction_id = int(callback.data.split(":", 1)[1])
    transaction = await db.get_transaction(user.user_id, transaction_id)
    if transaction is None:
        await callback.answer("Запись уже удалена", show_alert=True)
        return
    categories = await db.list_categories(user.user_id, transaction.kind)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(
            reply_markup=category_picker(transaction_id, categories)
        )


@router.callback_query(F.data.startswith("setcat:"))
async def cb_set_category(
    callback: CallbackQuery, db: Database, user: UserSettings, today: dt.date
) -> None:
    _, raw_transaction, raw_category = callback.data.split(":")
    transaction = await db.set_transaction_category(
        user.user_id, int(raw_transaction), int(raw_category)
    )
    if transaction is None:
        await callback.answer("Запись уже удалена", show_alert=True)
        return

    # Запоминаем выбор: слово из заметки закрепляем за выбранной категорией.
    learned = first_keyword(transaction.note)
    if learned:
        await db.add_keyword(user.user_id, int(raw_category), learned)

    await callback.answer("Категория обновлена")
    if isinstance(callback.message, Message):
        text = render_transaction(transaction, user.currency, today)
        if learned:
            text += f"\n<i>Запомнил: «{esc(learned)}» → {esc(transaction.category_title)}</i>"
        await callback.message.edit_text(
            text, reply_markup=transaction_actions(transaction)
        )


@router.callback_query(F.data.startswith("opback:"))
async def cb_back(
    callback: CallbackQuery, db: Database, user: UserSettings, today: dt.date
) -> None:
    transaction_id = int(callback.data.split(":", 1)[1])
    transaction = await db.get_transaction(user.user_id, transaction_id)
    await callback.answer()
    if transaction is None or not isinstance(callback.message, Message):
        return
    await callback.message.edit_text(
        render_transaction(transaction, user.currency, today),
        reply_markup=transaction_actions(transaction),
    )


__all__ = ["router", "save_transaction", "SAVED", "INVALID", "NOT_FOUND", "HINT",
           "EXPENSE", "INCOME", "Transaction"]
