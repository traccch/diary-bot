"""Напоминания: /remind, /reminders и кнопки под ними."""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from ..db import Database, UserSettings
from ..formatting import esc, plural
from ..keyboards import reminder_list
from ..parsing import parse_time
from ..reminders import SNOOZE_MINUTES

router = Router(name="reminders")

MAX_REMINDERS = 8

USAGE = (
    "⏰ <b>Напоминания</b>\n\n"
    "<code>/remind 08:00</code> — напоминать каждый день в 8 утра\n"
    "<code>/remind 21:00</code> — можно добавить сколько нужно\n"
    "<code>/remind off</code> — выключить все\n"
    "/reminders — список и настройки\n\n"
    "<i>Кардиологи обычно просят мерить утром до лекарств и вечером — "
    "два напоминания закрывают эту схему.</i>"
)


async def _refresh_list(message: Message, user: UserSettings, reminders) -> None:
    """Перерисовывает список после кнопки. Тот же текст Telegram считает ошибкой."""
    try:
        await message.edit_text(
            _list_text(user, reminders),
            reply_markup=reminder_list(reminders, user.skip_if_measured) if reminders else None,
        )
    except TelegramBadRequest:
        pass


def _list_text(user: UserSettings, reminders) -> str:
    if not reminders:
        return (
            "⏰ Напоминаний нет.\n\n"
            "Добавить: <code>/remind 08:00</code>"
        )
    times = ", ".join(f"<b>{reminder.label}</b>" for reminder in reminders)
    word = plural(len(reminders), "напоминание", "напоминания", "напоминаний")
    skip = (
        "Если за последние полтора часа измерение уже есть — промолчу."
        if user.skip_if_measured
        else "Напомню в любом случае, даже если измерение уже есть."
    )
    return (
        f"⏰ <b>{len(reminders)} {word}</b>: {times}\n"
        f"Часовой пояс: {esc(user.tz)} · сменить — /tz\n\n"
        f"{skip}\n\n"
        "<i>Нажми на время, чтобы удалить.</i>"
    )


@router.message(Command("remind"))
async def cmd_remind(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    raw = (command.args or "").strip().lower()
    if not raw:
        await message.answer(USAGE)
        return

    if raw in {"off", "выкл", "стоп", "0"}:
        removed = await db.delete_all_reminders(user.user_id)
        await message.answer(
            "Выключил все напоминания." if removed else "Напоминаний и так не было."
        )
        return

    at = parse_time(raw)
    if at is None:
        await message.answer(
            "Не понял время. Нужно так: <code>/remind 08:00</code> "
            "(или <code>/remind 8</code>)."
        )
        return

    existing = await db.list_reminders(user.user_id)
    if len(existing) >= MAX_REMINDERS:
        await message.answer(
            f"Больше {MAX_REMINDERS} напоминаний — это уже перебор. "
            "Удали лишние в /reminders."
        )
        return

    if await db.add_reminder(user.user_id, at) is None:
        await message.answer(f"Напоминание на {at:%H:%M} уже есть.")
        return

    reminders = await db.list_reminders(user.user_id)
    times = ", ".join(reminder.label for reminder in reminders)
    await message.answer(
        f"✅ Буду напоминать в <b>{at:%H:%M}</b> по часовому поясу {esc(user.tz)}.\n"
        f"Сейчас в списке: {times}"
    )


@router.message(Command("reminders"))
async def cmd_reminders(message: Message, db: Database, user: UserSettings) -> None:
    reminders = await db.list_reminders(user.user_id)
    await message.answer(
        _list_text(user, reminders),
        reply_markup=reminder_list(reminders, user.skip_if_measured) if reminders else None,
    )


@router.callback_query(F.data.startswith("remdel:"))
async def cb_reminder_delete(
    callback: CallbackQuery, db: Database, user: UserSettings
) -> None:
    at = parse_time(callback.data.split(":", 1)[1])
    if at is None or not await db.delete_reminder(user.user_id, at):
        await callback.answer("Этого напоминания уже нет")
    else:
        await callback.answer(f"Удалил {at:%H:%M}")

    reminders = await db.list_reminders(user.user_id)
    if isinstance(callback.message, Message):
        await _refresh_list(callback.message, user, reminders)


@router.callback_query(F.data == "remskip")
async def cb_toggle_skip(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    value = not user.skip_if_measured
    await db.set_skip_if_measured(user.user_id, value)
    await callback.answer(
        "Не буду дублировать напоминания" if value else "Буду напоминать всегда"
    )

    reminders = await db.list_reminders(user.user_id)
    updated = await db.ensure_user(user.user_id)
    if isinstance(callback.message, Message):
        await _refresh_list(callback.message, updated, reminders)


@router.callback_query(F.data == "rem:snooze")
async def cb_snooze(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    fire_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(
        minutes=SNOOZE_MINUTES
    )
    await db.add_snooze(user.user_id, fire_at)
    await callback.answer(f"Напомню через {SNOOZE_MINUTES} минут")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
