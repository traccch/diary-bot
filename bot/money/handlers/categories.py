"""Категории и лимиты: /cats, /addcat, /delcat, /kw, /limit, /limits."""

from __future__ import annotations

import datetime as dt
import re

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ...db import Database, UserSettings
from ...formatting import esc, format_money
from ..db import EXPENSE, INCOME, TOTAL_LIMIT_CATEGORY
from ..parsing import MAX_AMOUNT_MINOR, parse_amount
from ..stats import limit_line, month_end, month_start

router = Router(name="money-categories")

#: любой символ вне букв/цифр/пунктуации ASCII считаем эмодзи
_EMOJI = re.compile(r"^[^\w\s,]{1,4}", re.UNICODE)

LIMIT_USAGE = (
    "Формат:\n"
    "<code>/limit 30000</code> — общий лимит на месяц\n"
    "<code>/limit Кафе 8000</code> — лимит по категории\n"
    "<code>/limit Кафе 0</code> — снять лимит"
)


def _split_emoji(raw: str) -> tuple[str, str]:
    """«🍕 Пицца» → ('🍕', 'Пицца'); без эмодзи вернёт дефолтное."""
    raw = raw.strip()
    match = _EMOJI.match(raw)
    if match:
        return match.group(0), raw[match.end() :].strip()
    return "📦", raw


def _kind_of(args: str) -> tuple[str, str]:
    """Понимает приставку «доход» в командах категорий."""
    lowered = args.lower()
    for prefix in ("доход ", "доходы ", "+"):
        if lowered.startswith(prefix):
            return INCOME, args[len(prefix) :].strip()
    return EXPENSE, args


@router.message(Command("cats", "categories"))
async def cmd_cats(message: Message, db: Database, user: UserSettings) -> None:
    lines = ["🗂 <b>Категории</b>"]
    for kind, title in ((EXPENSE, "Расходы"), (INCOME, "Доходы")):
        categories = await db.list_categories(user.user_id, kind)
        lines.append("")
        lines.append(f"<b>{title}</b>")
        for category in categories:
            keywords = ", ".join(category.keywords[:8])
            if len(category.keywords) > 8:
                keywords += f" … (+{len(category.keywords) - 8})"
            suffix = f"\n    <i>{esc(keywords)}</i>" if keywords else ""
            lines.append(f"{esc(category.title)}{suffix}")

    lines.append("")
    lines.append(
        "Добавить: <code>/addcat 🍕 Пицца, пицца, додо</code>\n"
        "Доходную: <code>/addcat доход 🏦 Аренда, арендаторы</code>\n"
        "Удалить: <code>/delcat Пицца</code>\n"
        "Привязать слово: <code>/kw Кафе, шаурма</code>"
    )
    await message.answer("\n".join(lines))


@router.message(Command("addcat"))
async def cmd_addcat(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Формат: <code>/addcat 🍕 Пицца, пицца, додо</code>\n"
            "После названия через запятую — ключевые слова (необязательно).\n"
            "Для дохода: <code>/addcat доход 🏦 Аренда</code>"
        )
        return

    kind, args = _kind_of(args)
    head, *keywords = [part.strip() for part in args.split(",")]
    emoji, name = _split_emoji(head)
    if not name:
        await message.answer("Не понял название категории.")
        return

    category = await db.add_category(user.user_id, name, emoji, keywords, kind)
    if category is None:
        await message.answer(f"Категория «{esc(name)}» уже есть.")
        return
    word = "доходов" if kind == INCOME else "расходов"
    await message.answer(f"Добавил категорию {word}: {esc(category.title)}")


@router.message(Command("delcat"))
async def cmd_delcat(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    name = (command.args or "").strip()
    if not name:
        await message.answer("Формат: <code>/delcat Пицца</code>")
        return

    kind, name = _kind_of(name)
    category = await db.find_category_by_name(user.user_id, name, kind)
    if category is None:
        await message.answer(f"Категории «{esc(name)}» нет. Список — /cats")
        return
    if not await db.delete_category(user.user_id, category.id):
        await message.answer("Запасную категорию удалить нельзя — в неё падает остальное.")
        return
    await message.answer(f"Удалил {esc(category.title)}. Записи уехали в запасную категорию.")


@router.message(Command("kw"))
async def cmd_keyword(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    args = (command.args or "").strip()
    if "," not in args:
        await message.answer("Формат: <code>/kw Кафе, шаурма</code>")
        return

    kind, args = _kind_of(args)
    name, keyword = [part.strip() for part in args.split(",", 1)]
    category = await db.find_category_by_name(user.user_id, name, kind)
    if category is None:
        await message.answer(f"Категории «{esc(name)}» нет. Список — /cats")
        return

    await db.add_keyword(user.user_id, category.id, keyword)
    await message.answer(f"Запомнил: «{esc(keyword)}» → {esc(category.title)}")


@router.message(Command("limit"))
async def cmd_limit(
    message: Message,
    command: CommandObject,
    db: Database,
    user: UserSettings,
    today: dt.date,
) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(LIMIT_USAGE)
        return

    parsed = parse_amount(args)
    if parsed is None:
        await message.answer("Не нашёл сумму лимита.\n\n" + LIMIT_USAGE)
        return

    amount, rest = parsed
    if amount >= MAX_AMOUNT_MINOR:
        await message.answer("Слишком большая сумма — похоже на опечатку.")
        return

    category_id = TOTAL_LIMIT_CATEGORY
    label = "общий лимит на месяц"
    if rest:
        category = await db.find_category_by_name(user.user_id, rest, EXPENSE)
        if category is None:
            await message.answer(f"Категории «{esc(rest)}» нет. Список — /cats")
            return
        category_id, label = category.id, f"лимит {category.title}"

    if amount == 0:
        removed = await db.delete_limit(user.user_id, category_id)
        await message.answer(
            f"Снял {label}." if removed else f"А {label} и не был установлен."
        )
        return

    await db.set_limit(user.user_id, category_id, amount)
    await message.answer(
        f"Установил {label}: <b>{format_money(amount, user.currency)}</b>"
    )


@router.message(Command("limits"))
async def cmd_limits(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    limits = await db.list_limits(user.user_id)
    if not limits:
        await message.answer("Лимитов нет.\n\n" + LIMIT_USAGE)
        return

    start, end = month_start(today), month_end(today)
    lines = ["🎯 <b>Лимиты на месяц</b>", ""]
    for category_id, amount in limits:
        if category_id == TOTAL_LIMIT_CATEGORY:
            spent, _ = await db.total_between(user.user_id, start, end, EXPENSE)
            title = "Всего за месяц"
        else:
            category = await db.get_category(user.user_id, category_id)
            if category is None:
                continue
            spent = await db.category_total_between(user.user_id, category_id, start, end)
            title = category.title
        lines.append(f"<b>{esc(title)}</b>")
        lines.append(limit_line(spent, amount, user.currency))
        lines.append("")
    await message.answer("\n".join(lines).strip())
