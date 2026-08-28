"""Пробег: /car, запись числом и утреннее напоминание.

Свободный текст сюда попадает раньше денег: «пробег 203116» иначе стал бы
тратой в двести тысяч рублей.
"""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database, UserSettings
from . import stats
from .db import MAX_KM
from .parsing import parse_mileage

router = Router(name="car")

ASK = (
    "🚗 <b>Пробег на утро</b>\n"
    "Пришли число с одометра — например <code>203116</code>."
)


class CarEntry(StatesGroup):
    waiting_km = State()


def entry_actions(last_km: int = 0) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="✍️ Записать", callback_data="car:add")]]
    if last_km:
        # «не ездил» бережёт непрерывность ряда: пропуск дня превращает
        # вчерашние километры в сегодняшние
        rows[0].append(
            InlineKeyboardButton(text="🅿️ Не ездил", callback_data="car:same")
        )
    rows.append(
        [InlineKeyboardButton(text="⏱ Позже", callback_data="rem:snooze:car")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def save(
    message: Message, km: int, db: Database, user: UserSettings, today: dt.date
) -> None:
    """Записывает показание и говорит, что из него следует."""
    last = await db.last_reading(user.user_id)
    await db.set_reading(user.user_id, today, km)

    lines = [f"🚗 Записал: <b>{stats.format_km(km)} км</b>"]
    if last is not None and last.on_date != today:
        driven = km - last.km
        days = (today - last.on_date).days
        if driven < 0:
            lines.append(
                f"⚠️ Прошлое показание было больше — {stats.format_km(last.km)} км. "
                "Если это опечатка, пришли верное число."
            )
        elif driven:
            since = "со вчера" if days == 1 else f"за {days} дн."
            lines.append(f"Проехал {since}: <b>{stats.format_km(driven)} км</b>")
        else:
            lines.append("Со вчера никуда не ездил.")
    await message.answer("\n".join(lines))


@router.message(Command("car", "mileage"))
async def cmd_car(
    message: Message, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    await message.answer(await stats.build_report(db, user, now.date()))


@router.callback_query(F.data == "car:add")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CarEntry.waiting_km)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(ASK + "\n\n<i>Передумал — /cancel</i>")


@router.callback_query(F.data == "car:same")
async def cb_same(
    callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    last = await db.last_reading(user.user_id)
    if last is None:
        await callback.answer("Прошлого показания нет", show_alert=True)
        return

    await db.set_reading(user.user_id, now.date(), last.km)
    await callback.answer("Записал: не ездил")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"🅿️ Сегодня не ездил · на одометре {stats.format_km(last.km)} км",
            reply_markup=None,
        )


@router.message(CarEntry.waiting_km, Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменил. Пробег можно прислать и так: <code>пробег 203116</code>")


@router.message(CarEntry.waiting_km, F.text, ~F.text.startswith("/"))
async def state_km(
    message: Message,
    state: FSMContext,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
) -> None:
    text = (message.text or "").strip()
    km = parse_mileage(text) or (int(text) if text.isdigit() else None)
    if km is None or not 0 < km <= MAX_KM:
        await message.answer(
            "Нужно число с одометра, например <code>203116</code>.\n"
            "<i>Выйти — /cancel</i>"
        )
        return

    await state.clear()
    await save(message, km, db, user, now.date())


async def try_mileage(
    message: Message, text: str, db: Database, user: UserSettings, today: dt.date
) -> bool:
    """Записывает пробег, если он есть в строке. Остальное разберут дальше."""
    km = parse_mileage(text)
    if km is None:
        return False
    await save(message, km, db, user, today)
    return True
