"""Круг «выгрузил → показал ИИ → залил правки»: /import и приём файла."""

from __future__ import annotations

import datetime as dt
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import transfer
from ..db import Database, UserSettings
from ..formatting import esc, measurements_word
from ..keyboards import confirm_import

router = Router(name="transfer")

#: Файл больше — это точно не наша выгрузка.
MAX_FILE_BYTES = 5 * 1024 * 1024
#: Сколько правок показываем в предпросмотре целиком.
PREVIEW_LIMIT = 12

HOW_TO = (
    "📥 <b>Как залить правки</b>\n\n"
    "1. Возьми файл из /export → «🤖 JSON для ИИ»\n"
    "2. Отправь его любому ИИ с просьбой проверить записи на опечатки\n"
    "3. Полученный файл пришли сюда обычным вложением\n\n"
    "Я покажу, что изменится, и применю только после твоего подтверждения. "
    "Записи, которых в файле нет, не трогаю.\n\n"
    "<i>ИИ здесь — корректор опечаток, а не врач: он правит ввод, а не "
    "толкует цифры.</i>"
)


def _when(moment: dt.datetime) -> str:
    return moment.strftime("%d.%m %H:%M")


def _describe(change: transfer.Change) -> str:
    before = change.before
    if change.kind == "create":
        pulse = f" · ♥ {change.pulse}" if change.pulse else ""
        return (
            f"➕ <b>{change.systolic}/{change.diastolic}</b>{pulse} · "
            f"{_when(change.measured_at) if change.measured_at else ''}"
        )
    if change.kind == "delete" and before is not None:
        return f"🗑 <code>#{before.id}</code> {before.bp} · {_when(before.measured_at)}"

    assert before is not None
    parts: list[str] = []
    if change.systolic is not None or change.diastolic is not None:
        systolic = change.systolic or before.systolic
        diastolic = change.diastolic or before.diastolic
        parts.append(f"{before.bp} → <b>{systolic}/{diastolic}</b>")
    if change.pulse is not None:
        parts.append(f"♥ {before.pulse or '—'} → <b>{change.pulse}</b>")
    if change.measured_at is not None:
        parts.append(f"{_when(before.measured_at)} → <b>{_when(change.measured_at)}</b>")
    if change.note is not None:
        parts.append(f"«{esc(before.note)}» → «<b>{esc(change.note)}</b>»")
    return f"✏️ <code>#{before.id}</code> " + ", ".join(parts)


def _preview(plan: transfer.Plan) -> str:
    counts = []
    if plan.of("update"):
        counts.append(f"исправлю {len(plan.of('update'))}")
    if plan.of("create"):
        counts.append(f"добавлю {len(plan.of('create'))}")
    if plan.of("delete"):
        counts.append(f"удалю {len(plan.of('delete'))}")

    lines = ["📥 <b>Что изменится</b>", "", ", ".join(counts).capitalize() + "."]
    if plan.unchanged:
        lines.append(f"<i>Без изменений: {plan.unchanged}.</i>")
    if plan.skipped:
        lines.append(f"<i>Не разобрал строк: {plan.skipped}.</i>")
    lines.append("")

    lines.extend(_describe(change) for change in list(plan.changes)[:PREVIEW_LIMIT])
    if len(plan.changes) > PREVIEW_LIMIT:
        hidden = len(plan.changes) - PREVIEW_LIMIT
        lines.append(f"<i>…и ещё {hidden} {measurements_word(hidden)}</i>")
    return "\n".join(lines)


@router.message(Command("import"))
async def cmd_import(message: Message) -> None:
    await message.answer(HOW_TO)


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
            "Жду файл <code>.json</code> — тот, что отдаёт /export → «🤖 JSON для ИИ».\n"
            "CSV и PDF я обратно не читаю."
        )
        return
    if (document.file_size or 0) > MAX_FILE_BYTES:
        await message.answer("Файл слишком большой — это точно моя выгрузка?")
        return

    buffer = BytesIO()
    await bot.download(document, destination=buffer)

    existing = await db.last_measurements(user.user_id, limit=transfer.MAX_ROWS)
    try:
        plan = transfer.parse(buffer.getvalue(), existing, now)
    except transfer.ImportError_ as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return

    if not plan:
        await message.answer(
            "Расхождений с дневником нет — менять нечего."
            + (f"\n<i>Не разобрал строк: {plan.skipped}.</i>" if plan.skipped else "")
        )
        return

    await state.update_data(import_file_id=document.file_id)
    await message.answer(_preview(plan), reply_markup=confirm_import())


@router.callback_query(F.data == "import:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(import_file_id=None)
    await callback.answer("Отменено")
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Отменил. Ничего не изменилось.", reply_markup=None)


@router.callback_query(F.data == "import:apply")
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

    # Перечитываем файл и пересобираем план: дневник мог измениться, пока
    # пользователь смотрел предпросмотр.
    buffer = BytesIO()
    await bot.download(file_id, destination=buffer)
    existing = await db.last_measurements(user.user_id, limit=transfer.MAX_ROWS)
    try:
        plan = transfer.parse(buffer.getvalue(), existing, now)
    except transfer.ImportError_ as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return

    updated = created = deleted = 0
    for change in plan.changes:
        if change.kind == "delete" and change.measurement_id is not None:
            deleted += int(await db.delete_measurement(user.user_id, change.measurement_id))
        elif change.kind == "create":
            await db.add_measurement(
                user_id=user.user_id,
                systolic=change.systolic or 0,
                diastolic=change.diastolic or 0,
                pulse=change.pulse,
                measured_at=change.measured_at or now,
                note=change.note or "",
            )
            created += 1
        elif change.measurement_id is not None:
            result = await db.update_measurement(
                user.user_id,
                change.measurement_id,
                systolic=change.systolic,
                diastolic=change.diastolic,
                pulse=change.pulse,
                measured_at=change.measured_at,
                note=change.note,
            )
            updated += int(result is not None)

    await state.update_data(import_file_id=None)
    await callback.answer("Готово")
    if isinstance(callback.message, Message):
        summary = ", ".join(
            part
            for part in (
                f"исправлено {updated}" if updated else "",
                f"добавлено {created}" if created else "",
                f"удалено {deleted}" if deleted else "",
            )
            if part
        )
        await callback.message.edit_text(
            f"✅ Применил: {summary or 'ничего'}.\nПроверить — /last или /stats.",
            reply_markup=None,
        )
