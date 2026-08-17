"""Ввод измерений: свободный текст, пошаговый /add, правка и удаление."""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..ai import AiClient
from ..classify import alert
from ..db import Database, UserSettings
from ..formatting import esc, measurements_word, render_line, render_measurement, render_metric
from ..keyboards import delete_buttons, measurement_actions, skip_note
from ..parsing import (
    ParseError,
    looks_like_numbers,
    parse_entry,
    parse_measurement,
)
from ..stats import summarize
from .ai_common import save_ai_entry

router = Router(name="entry")

HINT = (
    "Не понял, где тут цифры. Вот как я понимаю:\n"
    "· <code>120/80 68</code> — давление и пульс\n"
    "· <code>120/80 68 сон 23:21-7:01</code> — заодно и сон\n"
    "· <code>шаги 8200 пульс покоя 58</code> — вечером с браслета\n"
    "· <code>вес 78,5</code> — когда встал на весы\n\n"
    "Все команды — /help"
)

ASK_BP = (
    "Что показал тонометр? Пришли цифры: <code>120/80</code> "
    "или <code>120/80 68</code> с пульсом."
)


class Entry(StatesGroup):
    waiting_bp = State()
    waiting_note = State()


async def _context_line(
    db: Database, user: UserSettings, now: dt.datetime
) -> str | None:
    """Одна строка про недельный фон — чтобы разовая цифра не пугала зря."""
    week = await db.measurements_between(
        user.user_id,
        dt.datetime.combine(now.date() - dt.timedelta(days=6), dt.time.min),
        now.replace(hour=23, minute=59),
    )
    summary = summarize(week, user.target_sys, user.target_dia)
    if summary is None or summary.count < 3:
        return None
    return (
        f"<i>За 7 дней: {summary.avg_sys}/{summary.avg_dia} в среднем, "
        f"{summary.count} {measurements_word(summary.count)}.</i>"
    )


#: Чем закончилась попытка записать измерение.
SAVED = "saved"
INVALID = "invalid"  # цифры есть, но бессмысленные — ошибка уже показана
NOT_FOUND = "not_found"  # давления в тексте нет


async def save_measurement(
    message: Message, text: str, db: Database, user: UserSettings, now: dt.datetime
) -> str:
    """Разбирает текст и сохраняет запись. Возвращает SAVED / INVALID / NOT_FOUND.

    В одном сообщении могут приехать и давление, и показатели здоровья
    («120/80 68 сон 23:21-7:01»), и только показатели («шаги 8200»).
    """
    try:
        entry = parse_entry(text, now)
    except ParseError as exc:
        await message.answer(f"⚠️ {exc}")
        return INVALID

    if not entry:
        return NOT_FOUND

    saved_metrics = [
        await db.set_metric(user.user_id, item.kind, entry.on_date, item.value, item.extra)
        for item in entry.metrics
    ]
    metric_lines = [render_metric(metric, now.date()) for metric in saved_metrics]

    if entry.measurement is None:
        await message.answer("✅ Записал\n\n" + "\n".join(metric_lines))
        return SAVED

    parsed = entry.measurement
    measurement = await db.add_measurement(
        user_id=user.user_id,
        systolic=parsed.systolic,
        diastolic=parsed.diastolic,
        pulse=parsed.pulse,
        measured_at=parsed.measured_at,
        note=parsed.note,
    )

    blocks = [render_measurement(measurement, now)]
    if metric_lines:
        blocks.append("\n".join(metric_lines))

    warning = alert(measurement.systolic, measurement.diastolic, measurement.pulse)
    if warning:
        blocks.append(warning)

    context = await _context_line(db, user, now)
    if context:
        blocks.append(context)

    if await db.count_measurements(user.user_id) == 1:
        blocks.append(
            "<i>Совет: включи напоминания — <code>/remind 08:00</code> и "
            "<code>/remind 21:00</code>. Кардиологу нужна регулярность, а не разовые замеры.</i>"
        )

    await message.answer(
        "\n\n".join(blocks), reply_markup=measurement_actions(measurement)
    )
    return SAVED


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("Отменил. Можно просто прислать цифры: <code>120/80</code>")


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    await state.set_state(Entry.waiting_bp)
    await message.answer(ASK_BP + "\n\n<i>Передумал — /cancel</i>")


@router.message(Entry.waiting_bp, F.text, ~F.text.startswith("/"))
async def state_bp(
    message: Message,
    state: FSMContext,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
) -> None:
    result = await save_measurement(message, message.text or "", db, user, now)
    if result == SAVED:
        await state.clear()
    elif result == NOT_FOUND:
        await message.answer(
            "Пока не вижу давления. Нужны два числа, например <code>128/84</code>.\n"
            "<i>Выйти — /cancel</i>"
        )


@router.message(Entry.waiting_note, F.text, ~F.text.startswith("/"))
async def state_note(
    message: Message,
    state: FSMContext,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
) -> None:
    data = await state.get_data()
    await state.clear()

    measurement_id = int(data.get("measurement_id", 0))
    note = (message.text or "").strip()[:200]
    measurement = await db.set_note(user.user_id, measurement_id, note)
    if measurement is None:
        await message.answer("Это измерение уже удалено.")
        return
    await message.answer(
        render_measurement(measurement, now), reply_markup=measurement_actions(measurement)
    )


@router.message(Command("last"))
async def cmd_last(
    message: Message, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    measurements = await db.last_measurements(user.user_id, limit=10)
    if not measurements:
        await message.answer("Дневник пуст. Пришли первое измерение: <code>120/80</code>")
        return

    lines = [f"🗒 <b>Последние {len(measurements)} {measurements_word(len(measurements))}</b>", ""]
    lines.extend(render_line(measurement, now) for measurement in measurements)
    await message.answer("\n".join(lines), reply_markup=delete_buttons(measurements))


@router.message(Command("undo"))
async def cmd_undo(message: Message, db: Database, user: UserSettings) -> None:
    recent = await db.last_measurements(user.user_id, limit=1)
    if not recent:
        await message.answer("Удалять нечего — дневник пуст.")
        return
    measurement = recent[0]
    await db.delete_measurement(user.user_id, measurement.id)
    await message.answer(f"🗑 Удалил измерение {measurement.bp}.")


@router.message(Command("edit"))
async def cmd_edit(
    message: Message, command: CommandObject, db: Database, user: UserSettings,
    now: dt.datetime,
) -> None:
    """Правка уже записанного измерения: /edit 42 120/80 68."""
    parts = (command.args or "").strip().lstrip("#").split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        await message.answer(
            "Формат: <code>/edit 42 120/80 68</code> — заменит цифры у записи #42.\n"
            "Номер записи виден в /last."
        )
        return

    measurement_id = int(parts[0])
    try:
        parsed = parse_measurement(parts[1], now) if len(parts) > 1 else None
    except ParseError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    if parsed is None:
        await message.answer(
            "Не понял новые цифры. Например: <code>/edit 42 120/80 68</code>"
        )
        return

    updated = await db.update_measurement(
        user.user_id,
        measurement_id,
        systolic=parsed.systolic,
        diastolic=parsed.diastolic,
        pulse=parsed.pulse,
        note=parsed.note or None,
    )
    if updated is None:
        await message.answer("Такой записи нет. Список — /last")
        return

    await message.answer(
        "✏️ Исправил:\n\n" + render_measurement(updated, now),
        reply_markup=measurement_actions(updated),
    )


@router.message(Command("del"))
async def cmd_del(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    raw = (command.args or "").strip().lstrip("#")
    if not raw.isdigit():
        await message.answer("Укажи номер: <code>/del 42</code> (номера есть в /last).")
        return
    if await db.delete_measurement(user.user_id, int(raw)):
        await message.answer(f"🗑 Измерение #{int(raw)} удалено.")
    else:
        await message.answer("Такого измерения нет.")


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def free_text(
    message: Message, db: Database, user: UserSettings, now: dt.datetime, ai: AiClient
) -> None:
    text = message.text or ""
    if await save_measurement(message, text, db, user, now) != NOT_FOUND:
        return

    # Обычный разбор не нашёл цифр — может быть, они названы словами
    # («давление сто тридцать на восемьдесят»). Без ключа шаг пропускается.
    if ai.available():
        result = await ai.extract_from_text(text, now)
        if result:
            await message.answer(await save_ai_entry(db, user, now, result))
            return

    if looks_like_numbers(text):
        await message.answer(HINT)
    else:
        await message.answer(
            "Это я записать не смогу. Я про давление и самочувствие: "
            "<code>120/80 68</code>, <code>сон 23:21-7:01</code>, "
            "<code>шаги 8200</code>, <code>вес 78,5</code>.\n\nВсе команды — /help"
        )


# ------------------------------------------------------------------ кнопки


@router.callback_query(F.data == "note:skip")
async def cb_note_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Ок, без комментария")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("note:"))
async def cb_note(callback: CallbackQuery, state: FSMContext, db: Database, user: UserSettings) -> None:
    measurement_id = int(callback.data.split(":", 1)[1])
    measurement = await db.get_measurement(user.user_id, measurement_id)
    if measurement is None:
        await callback.answer("Измерение уже удалено", show_alert=True)
        return

    await state.set_state(Entry.waiting_note)
    await state.update_data(measurement_id=measurement_id)
    await callback.answer()
    if isinstance(callback.message, Message):
        current = f"\nСейчас: <i>{esc(measurement.note)}</i>" if measurement.note else ""
        await callback.message.answer(
            f"Напиши комментарий к измерению {measurement.bp} "
            f"(что было до замера, лекарства, самочувствие).{current}",
            reply_markup=skip_note(),
        )


@router.callback_query(F.data.startswith("del:"))
async def cb_delete(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    measurement_id = int(callback.data.split(":", 1)[1])
    deleted = await db.delete_measurement(user.user_id, measurement_id)
    await callback.answer("Удалено" if deleted else "Уже удалено")
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(
                f"🗑 Измерение #{measurement_id} удалено.", reply_markup=None
            )
        except TelegramBadRequest:
            pass  # уже отредактировано этим же текстом


@router.callback_query(F.data == "rem:write")
async def cb_write_now(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Entry.waiting_bp)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(ASK_BP)
