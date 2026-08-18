"""Напоминания: /remind, /reminders и кнопки под ними.

Напоминание принадлежит разделу: в давлении оно зовёт измериться, в деньгах —
записать траты. Раздел берётся текущий, поэтому команда везде одна и та же.
"""

from __future__ import annotations

import datetime as dt
from typing import Sequence

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from .. import sections
from ..db import Database, Reminder, UserSettings
from ..formatting import esc, plural
from ..keyboards import reminder_list
from ..pressure.parsing import parse_time
from ..reminders import SNOOZE_MINUTES

router = Router(name="reminders")

MAX_REMINDERS = 8

USAGE = (
    "⏰ <b>Напоминания</b>\n\n"
    "<code>/remind 08:00</code> — напоминать каждый день в 8 утра\n"
    "<code>/remind 21:00</code> — можно добавить сколько нужно\n"
    "<code>/remind off</code> — выключить в этом разделе\n"
    "/reminders — список и настройки\n\n"
    "<i>Напоминание привязано к текущему разделу: в давлении зовёт измериться, "
    "в деньгах — записать траты. Раздел переключается в /menu.</i>"
)


def _list_text(user: UserSettings, reminders: Sequence[Reminder]) -> str:
    if not reminders:
        return "⏰ Напоминаний нет.\n\nДобавить: <code>/remind 08:00</code>"

    lines = []
    for section in sections.SECTIONS:
        own = [item for item in reminders if item.topic == section.key]
        if not own:
            continue
        times = ", ".join(f"<b>{item.label}</b>" for item in own)
        lines.append(f"{section.label}: {times}")

    word = plural(len(reminders), "напоминание", "напоминания", "напоминаний")
    skip = (
        "Если за последние полтора часа измерение уже есть — про давление промолчу."
        if user.skip_if_measured
        else "Напомню в любом случае, даже если измерение уже есть."
    )
    return (
        f"⏰ <b>{len(reminders)} {word}</b>\n"
        + "\n".join(lines)
        + f"\nЧасовой пояс: {esc(user.tz)} · сменить — /tz\n\n"
        + f"{skip}\n\n<i>Нажми на время, чтобы удалить.</i>"
    )


async def _refresh_list(
    message: Message, user: UserSettings, reminders: Sequence[Reminder]
) -> None:
    """Перерисовывает список после кнопки. Тот же текст Telegram считает ошибкой."""
    try:
        await message.edit_text(
            _list_text(user, reminders),
            reply_markup=reminder_list(reminders, user.skip_if_measured) if reminders else None,
        )
    except TelegramBadRequest:
        pass


@router.message(Command("remind"))
async def cmd_remind(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    raw = (command.args or "").strip().lower()
    section = sections.section_of(user.section)
    if not raw:
        await message.answer(USAGE)
        return

    if raw in {"off", "выкл", "стоп", "0"}:
        removed = await db.delete_all_reminders(user.user_id, section.key)
        await message.answer(
            f"Выключил напоминания раздела {section.label}."
            if removed
            else f"В разделе {section.label} напоминаний и не было."
        )
        return

    at = parse_time(raw)
    if at is None:
        await message.answer(
            "Не понял время. Нужно так: <code>/remind 08:00</code> "
            "(или <code>/remind 8</code>)."
        )
        return

    if len(await db.list_reminders(user.user_id)) >= MAX_REMINDERS:
        await message.answer(
            f"Больше {MAX_REMINDERS} напоминаний — это уже перебор. "
            "Удали лишние в /reminders."
        )
        return

    if await db.add_reminder(user.user_id, at, section.key) is None:
        await message.answer(f"Напоминание на {at:%H:%M} в этом разделе уже есть.")
        return

    await message.answer(
        f"✅ {section.label}: буду напоминать в <b>{at:%H:%M}</b> "
        f"по часовому поясу {esc(user.tz)}.\n"
        "<i>Список — /reminders</i>"
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
    _, topic, raw_time = callback.data.split(":", 2)
    at = parse_time(raw_time)
    if at is None or not await db.delete_reminder(user.user_id, at, topic):
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


@router.callback_query(F.data.startswith("rem:snooze"))
async def cb_snooze(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    parts = callback.data.split(":")
    topic = parts[2] if len(parts) > 2 else sections.PRESSURE
    fire_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(
        minutes=SNOOZE_MINUTES
    )
    await db.add_snooze(user.user_id, fire_at, topic)
    await callback.answer(f"Напомню через {SNOOZE_MINUTES} минут")
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
