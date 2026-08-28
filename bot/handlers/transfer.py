"""Загрузка операций файлом: /import, приём документа, предпросмотр, запись."""

from __future__ import annotations

import datetime as dt
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .. import sections
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
    "Минус — расход, плюс — доход. Категорию подберу сам по заметке — "
    "или впиши её прямо: <code>\"category\": \"Продукты\"</code>.\n\n"
    "<i>Такой файл может собрать и любой ИИ: покажи ему свои записи и попроси "
    "перевести в этот формат. Я покажу, что получилось, и запишу только после "
    "твоего подтверждения. Уже записанное не задвоится.</i>\n\n"
    "<b>Тот же файл можно прислать повторно</b> — например, разобрав категории. "
    "Операции не задвоятся: у совпадающих я просто поправлю категорию и покажу, "
    "что именно меняю."
)


#: Больше в одно сообщение Telegram не пустит (лимит 4096 знаков).
CHUNK = 3500


def confirm_import(plan: transfer.Plan) -> InlineKeyboardMarkup:
    parts = []
    if plan.rows:
        parts.append(
            f"записать {len(plan.rows)} "
            + plural(len(plan.rows), "операцию", "операции", "операций")
        )
    if plan.fixes:
        parts.append(f"поправить {len(plan.fixes)}")
    rows = [
        [
            InlineKeyboardButton(
                text="✅ " + " и ".join(parts).capitalize(), callback_data="imp:apply"
            )
        ]
    ]
    hidden = max(0, len(plan.rows) - PREVIEW_LIMIT) + max(0, len(plan.fixes) - PREVIEW_LIMIT)
    if hidden:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👁 Показать все ({hidden} скрыто)", callback_data="imp:more"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="imp:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def full_list(plan: transfer.Plan, currency: str) -> list[str]:
    """Весь список целиком, разбитый на сообщения по длине.

    «…и ещё 38» — плохая концовка для списка, который человек как раз и хочет
    проверить: правки он подтверждает не глядя только один раз.
    """
    lines: list[str] = []
    if plan.rows:
        lines.append(f"📥 <b>Записать ({len(plan.rows)})</b>")
        for row in plan.rows:
            sign = "+" if row.kind == INCOME else "−"
            lines.append(
                f"{row.happened_on:%d.%m} {sign}{format_money(row.amount, currency)}"
                f" · {esc(row.note or '—')}"
                + (f" · {esc(row.category)}" if row.category else "")
            )
    if plan.fixes:
        if lines:
            lines.append("")
        lines.append(f"✏️ <b>Поправить категорию ({len(plan.fixes)})</b>")
        for fix in plan.fixes:
            lines.append(
                f"{fix.happened_on:%d.%m} {esc(fix.note or '—')}: "
                f"{esc(fix.was)} → <b>{esc(fix.becomes)}</b>"
            )

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) > CHUNK and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def fixes_block(plan: transfer.Plan) -> list[str]:
    """Что бот собирается переложить из категории в категорию."""
    if not plan.fixes:
        return []
    lines = [
        f"✏️ <b>Поправлю категорию у {len(plan.fixes)} </b>"
        + plural(len(plan.fixes), "операции", "операций", "операций"),
        "",
    ]
    for fix in plan.fixes[:PREVIEW_LIMIT]:
        lines.append(
            f"{fix.happened_on:%d.%m} {esc(fix.note or '—')}: "
            f"{esc(fix.was)} → <b>{esc(fix.becomes)}</b>"
        )
    if len(plan.fixes) > PREVIEW_LIMIT:
        lines.append(f"<i>…и ещё {len(plan.fixes) - PREVIEW_LIMIT}</i>")
    return lines


def preview(plan: transfer.Plan, currency: str) -> str:
    if not plan.rows:
        # в файле одни правки — про суммы и периоды говорить нечего
        return "\n".join(
            fixes_block(plan)
            + ([f"<i>Остальное уже записано: {plan.duplicates}.</i>"] if plan.duplicates else [])
        )

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

    if plan.fixes:
        lines.append("")
        lines.extend(fixes_block(plan))
    return "\n".join(lines)


def pick_category(row: transfer.Row, categories):
    """Названная в файле категория важнее угаданной по заметке.

    «электроэнергия, долг за три месяца» — это коммуналка, а не долг, и никакой
    разбор заметки этого не поймёт: слово в ней стоит честное. Поэтому файл
    может сказать прямо.
    """
    if row.category:
        wanted = row.category.lower().replace("ё", "е")
        for category in categories:
            if category.name.lower().replace("ё", "е") == wanted:
                return category
    return match_category(row.note, categories)


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
    await message.answer(preview(plan, user.currency), reply_markup=confirm_import(plan))


@router.callback_query(F.data == "imp:more")
async def cb_more(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    state: FSMContext,
) -> None:
    file_id = (await state.get_data()).get("import_file_id")
    message = callback.message
    if not file_id or not isinstance(message, Message):
        await callback.answer("Файл потерялся, пришли его заново", show_alert=True)
        return

    existing = await db.last_transactions(user.user_id, limit=transfer.MAX_ROWS)
    try:
        plan = transfer.parse(await _read(bot, file_id), now.date(), existing)
    except transfer.ImportError_ as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return

    await callback.answer()
    for chunk in full_list(plan, user.currency):
        await message.answer(chunk)


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

    fixed = 0
    for fix in plan.fixes:
        # категория должна быть того же вида: «Долги» есть и в расходах,
        # и в доходах, и это разные категории
        category = await db.find_category_by_name(user.user_id, fix.becomes, fix.kind)
        if category is None:
            continue
        fixed += int(
            await db.set_transaction_category(user.user_id, fix.transaction_id, category.id)
            is not None
        )

    written = 0
    for row in plan.rows:
        categories = await db.list_categories(user.user_id, row.kind)
        category = pick_category(row, categories) or await db.get_fallback_category(
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
    # раздел переключаем сразу: иначе следующая /stats покажет давление
    await db.set_section(user.user_id, sections.MONEY)
    await callback.answer("Готово")
    if isinstance(callback.message, Message):
        done = []
        if written:
            done.append(
                f"записал {written} "
                + plural(written, "операцию", "операции", "операций")
            )
        if fixed:
            done.append(f"поправил категорию у {fixed}")
        await callback.message.edit_text(
            "✅ " + (", ".join(done).capitalize() or "Ничего не изменилось") + ".",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📊 Сводка", callback_data="do:money:stats"
                        ),
                        InlineKeyboardButton(
                            text="💼 Баланс", callback_data="do:money:balance"
                        ),
                    ]
                ]
            ),
        )
