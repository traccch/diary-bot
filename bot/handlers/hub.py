"""Кнопочное меню: всё, что делаешь руками, — в два нажатия и без команд.

Список из тридцати команд честно описывает бота и никуда не годится как
интерфейс: его нужно прочитать, понять и запомнить, а нужен он ровно в тот
момент, когда хочется быстро что-то записать. Поэтому здесь то же самое, но
кнопками: экран «что сделать», под ним — разделы, под ними — действия.

Команды никуда не делись и остаются быстрым путём для тех, кто их помнит.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import prompts, sections
from ..db import Database, UserSettings
from ..english import handlers as english
from ..keyboards import health_prompt
from ..money.handlers import categories as money_categories
from ..money.handlers import entry as money_entry
from ..money.handlers import reports as money_reports
from ..pressure.handlers import entry as pressure_entry
from ..pressure.handlers import export as pressure_export
from ..pressure.handlers import reports as pressure_reports
from ..updater import UpdateError, Updater
from .update import is_owner, render_status
from ..keyboards import update_actions

router = Router(name="hub")

HOME = (
    "📔 <b>Что сделать?</b>\n\n"
    "Выбирай кнопкой — печатать команды не нужно.\n"
    "<i>Записать можно и просто текстом: <code>120/80 68</code>, "
    "<code>кофе 300</code>. Я разберу и так.</i>"
)

SCREENS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "bp": (
        "🩺 <b>Давление</b>",
        (
            ("✍️ Записать измерение", "do:bp:add"),
            ("📋 Последние", "do:bp:last"),
            ("📊 Сводка", "do:bp:stats"),
            ("📈 График", "do:bp:chart"),
            ("📄 Выгрузка для врача", "do:bp:export"),
            ("🗑 Удалить последнее", "do:bp:undo"),
        ),
    ),
    "money": (
        "💰 <b>Деньги</b>",
        (
            ("💸 Записать трату", "do:money:add"),
            ("💵 Записать доход", "do:money:income"),
            ("💼 Баланс за месяц", "do:money:balance"),
            ("📊 Сводка", "do:money:stats"),
            ("📋 Последние", "do:money:last"),
            ("🏷 Категории", "do:money:cats"),
            ("🎯 Лимиты", "do:money:limits"),
            ("📥 Загрузить файлом", "do:money:import"),
            ("🗑 Удалить последнюю", "do:money:undo"),
        ),
    ),
    "health": (
        "🫀 <b>Самочувствие</b>\n\nЧто записать за сегодня?",
        (
            ("😴 Сон", "do:health:sleep"),
            ("👟 Шаги", "do:health:steps"),
            ("💓 Пульс покоя", "do:health:resting_pulse"),
            ("⚖️ Вес", "do:health:weight"),
        ),
    ),
    "eng": (
        "🇬🇧 <b>Английский</b>",
        (
            ("▶️ Карточки на 3 минуты", "do:eng:cards"),
            ("🎮 Квест", "do:eng:quest"),
            ("📈 Прогресс", "do:eng:stats"),
        ),
    ),
    "settings": (
        "⚙️ <b>Настройки</b>",
        (
            ("⏰ Напоминания", "do:set:remind"),
            ("🎯 Цель по давлению", "do:set:target"),
            ("🌍 Часовой пояс", "do:set:tz"),
            ("⬇️ Обновить бота", "do:set:update"),
            ("📖 Все команды", "do:set:help"),
            ("ℹ️ О шкале и границах", "do:set:about"),
        ),
    ),
}


def home_keyboard():
    builder = InlineKeyboardBuilder()
    for text, data in (
        ("🩺 Давление", "do:bp"),
        ("💰 Деньги", "do:money"),
        ("🫀 Самочувствие", "do:health"),
        ("🇬🇧 Английский", "do:eng"),
        ("⚙️ Настройки", "do:settings"),
    ):
        builder.button(text=text, callback_data=data)
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def screen_keyboard(name: str):
    builder = InlineKeyboardBuilder()
    for text, data in SCREENS[name][1]:
        builder.button(text=text, callback_data=data)
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="← Назад", callback_data="do:home"))
    return builder.as_markup()


async def show_home(message: Message, edit: bool = False) -> None:
    if edit:
        try:
            await message.edit_text(HOME, reply_markup=home_keyboard())
            return
        except TelegramBadRequest:
            pass
    await message.answer(HOME, reply_markup=home_keyboard())


@router.callback_query(F.data == "do:home")
async def cb_home(callback: CallbackQuery) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await show_home(callback.message, edit=True)


@router.callback_query(F.data.in_({f"do:{name}" for name in SCREENS}))
async def cb_screen(callback: CallbackQuery) -> None:
    name = (callback.data or "").split(":")[1]
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(
            SCREENS[name][0], reply_markup=screen_keyboard(name)
        )
    except TelegramBadRequest:
        await callback.message.answer(
            SCREENS[name][0], reply_markup=screen_keyboard(name)
        )


async def _ensure_section(db: Database, user: UserSettings, key: str) -> UserSettings:
    """Действие из меню заодно открывает свой раздел — иначе /stats потом удивит."""
    if user.section == key:
        return user
    await db.set_section(user.user_id, key)
    return await db.ensure_user(user.user_id)


@router.callback_query(F.data.startswith("do:bp:"))
async def cb_pressure(
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

    user = await _ensure_section(db, user, sections.PRESSURE)
    await callback.answer()

    if action == "add":
        await pressure_entry.cmd_add(message, state)
    elif action == "last":
        await pressure_entry.cmd_last(message, db, user, now)
    elif action == "undo":
        await pressure_entry.cmd_undo(message, db, user)
    elif action == "stats":
        await pressure_reports.cmd_stats(message, db, user, now)
    elif action == "chart":
        await pressure_reports.cmd_chart(message, db, user, now)
    elif action == "export":
        await pressure_export.cmd_export(message, db, user)


@router.callback_query(F.data.startswith("do:money:"))
async def cb_money(
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

    user = await _ensure_section(db, user, sections.MONEY)
    await callback.answer()
    today = now.date()

    if action == "add":
        await money_entry.ask_expense(message, state)
    elif action == "income":
        await money_entry.ask_income(message, state)
    elif action == "balance":
        await money_reports.cmd_balance(message, db, user, today)
    elif action == "stats":
        await money_reports.cmd_stats(message, db, user, today)
    elif action == "last":
        await money_entry.cmd_last(message, db, user, today)
    elif action == "undo":
        await money_entry.cmd_undo(message, db, user)
    elif action == "cats":
        await money_categories.cmd_cats(message, db, user)
    elif action == "limits":
        await money_categories.cmd_limits(message, db, user, today)
    elif action == "import":
        from .transfer import HOW_TO

        await message.answer(HOW_TO)


@router.callback_query(F.data.startswith("do:health:"))
async def cb_health(
    callback: CallbackQuery, db: Database, user: UserSettings, now: dt.datetime
) -> None:
    kind = (callback.data or "").split(":")[2]
    prompt = prompts.BY_KIND.get(kind)
    message = callback.message
    if prompt is None or not isinstance(message, Message):
        await callback.answer()
        return

    await callback.answer()
    already = await db.get_metric(user.user_id, kind, now.date())
    known = (
        f"\n<i>Сегодня уже записано: {prompts.confirm(kind, already.value)[2:]}. "
        "Новое значение заменит прежнее.</i>"
        if already is not None
        else ""
    )
    await message.answer(
        prompts.render(prompt) + known, reply_markup=health_prompt(prompt)
    )


@router.callback_query(F.data.startswith("do:eng:"))
async def cb_english(
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

    user = await _ensure_section(db, user, sections.ENGLISH)
    await callback.answer()

    if action == "cards":
        await english.session.start_session(message, db, user, now.date(), state)
    elif action == "quest":
        await english.quest.open_quest(message, db, user, state)
    elif action == "stats":
        await english.progress.show_progress(message, db, user, now.date())


@router.callback_query(F.data.startswith("do:set:"))
async def cb_settings(
    callback: CallbackQuery,
    db: Database,
    user: UserSettings,
    updater: Updater,
    owner_id: Optional[int] = None,
) -> None:
    from ..keyboards import reminder_list
    from . import common
    from .reminders import _list_text

    action = (callback.data or "").split(":")[2]
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return

    await callback.answer()

    if action == "remind":
        reminders = await db.list_reminders(user.user_id)
        await message.answer(
            _list_text(user, reminders),
            reply_markup=reminder_list(reminders, user.skip_if_measured)
            if reminders
            else None,
        )
    elif action == "target":
        await message.answer(
            f"🎯 Цель: <b>ниже {user.target_sys}/{user.target_dia}</b>\n"
            "Сменить — пришли <code>/target 130/80</code>.\n\n"
            "<i>По умолчанию 135/85 — порог для домашних измерений.</i>"
        )
    elif action == "tz":
        await message.answer(
            f"🌍 Часовой пояс: <b>{user.tz}</b>\n"
            "Сменить — пришли <code>/tz Asia/Almaty</code>.\n\n"
            "<i>По нему же считаются напоминания.</i>"
        )
    elif action == "help":
        await message.answer(common.HELP)
    elif action == "about":
        await message.answer(common.ABOUT)
    elif action == "update":
        user_id = callback.from_user.id if callback.from_user else 0
        if not await is_owner(db, owner_id, user_id):
            await message.answer("Обновлять бота может только владелец.")
            return
        try:
            status = await updater.check()
        except UpdateError as exc:
            await message.answer(f"⚠️ {exc}")
            return
        await message.answer(
            render_status(status),
            reply_markup=update_actions() if status.available else None,
        )
