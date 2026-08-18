"""Разделы: /menu и переключение кнопками."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from .. import sections
from ..db import Database, UserSettings
from ..keyboards import section_menu

router = Router(name="menu")


def menu_text(active: str) -> str:
    section = sections.section_of(active)
    lines = [f"📂 <b>Раздел: {section.label}</b>", ""]
    for item in sections.SECTIONS:
        mark = "▸" if item.key == active else "·"
        lines.append(f"{mark} {item.label} — {item.hint}")
    lines.append("")
    lines.append(
        "<i>Команды /stats, /last, /undo, /export работают в текущем разделе. "
        "Но цифры я узнаю в любом: давление запишется в дневник, даже когда "
        "открыты деньги.</i>"
    )
    return "\n".join(lines)


@router.message(Command("menu", "sections"))
async def cmd_menu(message: Message, user: UserSettings) -> None:
    await message.answer(menu_text(user.section), reply_markup=section_menu(user.section))


@router.callback_query(F.data.startswith("go:"))
async def cb_switch(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    key = callback.data.split(":", 1)[1]
    if key not in sections.BY_KEY:
        await callback.answer()
        return

    await db.set_section(user.user_id, key)
    section = sections.section_of(key)
    await callback.answer(f"Открыл: {section.title}")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(menu_text(key), reply_markup=section_menu(key))
