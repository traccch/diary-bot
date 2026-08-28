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
from . import service, stats
from .db import MAX_KM
from .parsing import parse_mileage

router = Router(name="car")

ASK = (
    "🚗 <b>Пробег на утро</b>\n"
    "Пришли число с одометра — например <code>203116</code>."
)


class CarEntry(StatesGroup):
    waiting_km = State()
    waiting_service_km = State()


#: Ключ пометки «когда снова можно спросить про ТО».
def ask_key(user_id: int) -> str:
    return f"car_service_ask:{user_id}"


def service_keyboard(km: int) -> InlineKeyboardMarkup:
    """Кнопки «через сколько ТО» — считаются от текущего пробега."""
    builder = []
    for step, target in service.targets(km):
        builder.append(
            InlineKeyboardButton(
                text=f"через {stats.format_km(step)} · {stats.format_km(target)}",
                callback_data=f"car:to:{target}",
            )
        )
    rows = [[button] for button in builder]
    rows.append(
        [
            InlineKeyboardButton(text="✍️ Другое число", callback_data="car:to:ask"),
            InlineKeyboardButton(text="Не веду ТО", callback_data="car:to:never"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ ТО сделано", callback_data="car:to:done")]
        ]
    )


ASK_SERVICE = (
    "🔧 <b>Когда ближайшее ТО?</b>\n"
    "Скажи один раз — дальше я промолчу, пока до него не останется "
    f"{stats.format_km(service.WARN_KM)} км."
)


async def should_ask_service(db: Database, user_id: int, today: dt.date) -> bool:
    """Спрашивать ли про ТО. Не чаще раза в неделю и только если оно не задано."""
    if await db.get_service(user_id) is not None:
        return False
    stamp = await db.get_meta(ask_key(user_id))
    if not stamp:
        return True
    try:
        return dt.date.fromisoformat(stamp) <= today
    except ValueError:
        return True


async def ask_service(
    message: Message, db: Database, user_id: int, km: int, today: dt.date
) -> None:
    await db.set_meta(ask_key(user_id), service.ask_again(today).isoformat())
    await message.answer(ASK_SERVICE, reply_markup=service_keyboard(km))


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

    plan = await db.get_service(user.user_id)
    warning = service.line(plan, km)
    if warning:
        lines.append("")
        lines.append(warning)

    await message.answer(
        "\n".join(lines),
        reply_markup=done_keyboard() if warning else None,
    )

    # про ТО спрашиваем ровно тогда, когда пробег уже перед глазами
    if await should_ask_service(db, user.user_id, today):
        await ask_service(message, db, user.user_id, km, today)


@router.callback_query(F.data.startswith("car:to:"))
async def cb_service(
    callback: CallbackQuery,
    db: Database,
    user: UserSettings,
    now: dt.datetime,
    state: FSMContext,
) -> None:
    action = (callback.data or "").split(":")[2]
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return

    last = await db.last_reading(user.user_id)
    km = last.km if last else 0

    if action == "ask":
        await state.set_state(CarEntry.waiting_service_km)
        await callback.answer()
        await message.answer(
            "На каком пробеге ТО? Пришли число — например "
            f"<code>{stats.format_km(km + 10000).replace(' ', '')}</code>.\n\n"
            "<i>Передумал — /cancel</i>"
        )
        return

    if action == "never":
        await db.set_meta(
            ask_key(user.user_id), service.ask_again(now.date(), muted=True).isoformat()
        )
        await callback.answer("Больше не спрашиваю")
        await message.edit_text("Хорошо, про ТО не спрашиваю.", reply_markup=None)
        return

    if action == "done":
        plan = await db.get_service(user.user_id)
        if plan is None:
            await callback.answer()
            return
        await db.complete_service(user.user_id, km)
        following = service.next_after_done(plan, km)
        await callback.answer("Записал")
        if following:
            await db.set_service(user.user_id, following, plan.interval_km)
            await message.answer(
                f"🔧 Отметил ТО на {stats.format_km(km)} км.\n"
                f"Следующее — {stats.format_km(following)} км, напомню заранее."
            )
        else:
            await db.clear_service(user.user_id)
            await message.answer(
                f"🔧 Отметил ТО на {stats.format_km(km)} км.", 
                reply_markup=service_keyboard(km),
            )
        return

    if not action.isdigit():
        await callback.answer()
        return

    due = int(action)
    await db.set_service(user.user_id, due, max(0, due - km))
    await callback.answer("Запомнил")
    await message.edit_text(
        f"🔧 ТО на <b>{stats.format_km(due)} км</b> — это через "
        f"{stats.format_km(max(0, due - km))} км.\n"
        f"<i>Напомню, когда останется {stats.format_km(service.WARN_KM)} км. "
        "До тех пор молчу.</i>",
        reply_markup=None,
    )


@router.message(CarEntry.waiting_service_km, Command("cancel"))
async def cmd_cancel_service(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменил.")


@router.message(CarEntry.waiting_service_km, F.text, ~F.text.startswith("/"))
async def state_service_km(
    message: Message,
    state: FSMContext,
    db: Database,
    user: UserSettings,
) -> None:
    digits = "".join(char for char in (message.text or "") if char.isdigit())
    due = int(digits) if digits else 0
    last = await db.last_reading(user.user_id)
    km = last.km if last else 0

    if not 0 < due <= MAX_KM:
        await message.answer("Нужно число — пробег, на котором ТО. <i>Выйти — /cancel</i>")
        return
    if due <= km:
        await message.answer(
            f"Это меньше текущего пробега ({stats.format_km(km)} км). "
            "Пришли пробег, на котором ТО будет. <i>Выйти — /cancel</i>"
        )
        return

    await state.clear()
    await db.set_service(user.user_id, due, due - km)
    await message.answer(
        f"🔧 ТО на <b>{stats.format_km(due)} км</b> — это через "
        f"{stats.format_km(due - km)} км. Напомню заранее."
    )


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
