"""Загрузка операций файлом: /import, приём документа, предпросмотр, запись."""

from __future__ import annotations

import datetime as dt
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database, UserSettings
from ..formatting import esc, format_money, plural
from ..money import transfer
from ..money.db import INCOME
from ..money.parsing import match_category

router = Router(name="transfer")

#: Файл больше — это точно не список операций.
MAX_FILE_BYTES = 5 * 1024 * 1024
#: Сколько строк показываем в предпросмотре.
PREVIEW_LIMIT = 8

HOW_TO = (
    "📥 <b>Загрузить операции файлом</b>\n\n"
    "Если траты копились в заметках или в переписке с собой, не нужно вбивать "
    "их по одной. Пришли сюда файл <code>.json</code> вот такого вида:\n\n"
    "<code>{\n"
    '  "transactions": [\n'
    '    {"date": "2026-08-11", "amount": -661, "note": "продукты"},\n'
    '    {"date": "2026-08-12", "amount": 215, "note": "кешбэк"}\n'
    "  ]\n"
    "}</code>\n\n"
    "Минус — расход, плюс — доход. Категорию подберу сам по заметке.\n\n"
    "<i>Такой файл может собрать и любой ИИ: покажи ему свои записи и попроси "
    "перевести в этот формат. Я покажу, что получилось, и запишу только после "
    "твоего подтверждения. Уже записанное не задвоится.</i>"
)


def confirm_import(count: int) -> InlineKeyboardMarkup:
    word = plural(count, "операцию", "операции", "операций")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Записать {count} {word}", callback_data="imp:apply"
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="imp:cancel")],
        ]
    )


def preview(plan: transfer.Plan, currency: str) -> str:
    period = plan.period
    lines = [f"📥 <b>Нашёл {len(plan.rows)} "
             + plural(len(plan.rows), "операцию", "операции", "операций") + "</b>"]
    if period is not None:
        first, last = period
        lines.append(
            f"<i>с {first:%d.%m} по {last:%d.%m}</i>"
            if first != last
            else f"<i>за {first:%d.%m}</i>"
        )
    lines.append("")
    lines.append(f"Расходы: <b>{format_money(plan.expense, currency)}</b>")
    lines.append(f"Доходы: <b>{format_money(plan.income, currency)}</b>")
    lines.append(
        f"Итог: <b>{format_money(plan.income - plan.expense, currency)}</b>"
    )

    if plan.duplicates:
        lines.append(
            f"<i>Уже записано раньше: {plan.duplicates} — пропущу.</i>"
        )
    if plan.skipped:
        lines.append(f"<i>Не разобрал строк: {plan.skipped}.</i>")

    lines.append("")
    for row in plan.rows[:PREVIEW_LIMIT]:
        sign = "+" if row.kind == INCOME else "−"
        lines.append(
            f"{row.happened_on:%d.%m} {sign}{format_money(row.amount, currency)} "
            f"· {esc(row.note or '—')}"
        )
    if len(plan.rows) > PREVIEW_LIMIT:
        hidden = len(plan.rows) - PREVIEW_LIMIT
        lines.append(f"<i>…и ещё {hidden}</i>")
    return "\n".join(lines)


@router.message(Command("import"))
async def cmd_import(message: Message) -> None:
    await message.answer(HOW_TO)


async def _read(bot: Bot, file) -> bytes:
    buffer = BytesIO()
    await bot.download(file, destination=buffer)
    return buffer.getvalue()


@router.message(F.document)
async def handle_document(
    message: Message,
    bot: Bot,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    state: FSMContext,
) -> None:
    document = message.document
    if document is None:
        return

    if not (document.file_name or "").lower().endswith(".json"):
        await message.answer(
            "Жду файл <code>.json</code> со списком операций — как это выглядит, "
            "покажет /import."
        )
        return
    if (document.file_size or 0) > MAX_FILE_BYTES:
        await message.answer("Файл слишком большой — это точно список операций?")
        return

    existing = await db.last_transactions(user.user_id, limit=transfer.MAX_ROWS)
    try:
        plan = transfer.parse(await _read(bot, document), now.date(), existing)
    except transfer.ImportError_ as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return

    if not plan:
        await message.answer(
            "Записывать нечего: всё из файла уже есть в дневнике."
            if plan.duplicates
            else "В файле не нашлось ни одной операции."
        )
        return

    await state.update_data(import_file_id=document.file_id)
    await message.answer(
        preview(plan, user.currency), reply_markup=confirm_import(len(plan.rows))
    )


@router.callback_query(F.data == "imp:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(import_file_id=None)
    await callback.answer("Отменено")
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Отменил. Ничего не записал.", reply_markup=None)


@router.callback_query(F.data == "imp:apply")
async def cb_apply(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    state: FSMContext,
) -> None:
    file_id = (await state.get_data()).get("import_file_id")
    if not file_id:
        await callback.answer("Файл потерялся, пришли его заново", show_alert=True)
        return

    # перечитываем файл: дневник мог пополниться, пока человек смотрел предпросмотр
    existing = await db.last_transactions(user.user_id, limit=transfer.MAX_ROWS)
    try:
        plan = transfer.parse(await _read(bot, file_id), now.date(), existing)
    except transfer.ImportError_ as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return

    written = 0
    for row in plan.rows:
        categories = await db.list_categories(user.user_id, row.kind)
        category = match_category(row.note, categories) or await db.get_fallback_category(
            user.user_id, row.kind
        )
        await db.add_transaction(
            user_id=user.user_id,
            kind=row.kind,
            amount=row.amount,
            note=row.note,
            happened_on=row.happened_on,
            category_id=category.id if category else None,
        )
        written += 1

    await state.update_data(import_file_id=None)
    await callback.answer("Готово")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"✅ Записал {written} "
            + plural(written, "операцию", "операции", "операций")
            + ".\nПосмотреть — /stats или /balance.",
            reply_markup=None,
        )
