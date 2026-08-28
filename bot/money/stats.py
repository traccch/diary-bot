"""Сводки раздела «Деньги»: периоды, разбивка по категориям, баланс, лимиты."""

from __future__ import annotations

import datetime as dt
from typing import Optional, Sequence

from ..db import Database, UserSettings
from ..formatting import bar, esc, format_date, format_money, plural
from .db import EXPENSE, INCOME, TOTAL_LIMIT_CATEGORY, CategoryTotal

PERIODS: tuple[tuple[str, str], ...] = (
    ("day", "Сегодня"),
    ("week", "Неделя"),
    ("month", "Месяц"),
    ("all", "Всё время"),
)

VALID_PERIODS = {key for key, _ in PERIODS}


def records_word(count: int) -> str:
    return plural(count, "запись", "записи", "записей")


def month_start(date: dt.date) -> dt.date:
    return date.replace(day=1)


def month_end(date: dt.date) -> dt.date:
    if date.month == 12:
        return date.replace(day=31)
    return date.replace(month=date.month + 1, day=1) - dt.timedelta(days=1)


def month_title(date: dt.date) -> str:
    from ..formatting import MONTHS_NOMINATIVE

    return f"{MONTHS_NOMINATIVE[date.month - 1]} {date.year}"


def period_range(period: str, today: dt.date) -> tuple[dt.date, dt.date, str]:
    """Возвращает (начало, конец, заголовок) для ключа периода."""
    if period == "day":
        return today, today, f"за {format_date(today, today)}"
    if period == "week":
        start = today - dt.timedelta(days=today.weekday())
        return start, today, "за неделю"
    if period == "all":
        return dt.date(1970, 1, 1), today, "за всё время"
    return month_start(today), today, f"за {month_title(today)}"


def render_breakdown(
    totals: Sequence[CategoryTotal], grand_total: int, currency: str
) -> list[str]:
    """Строки разбивки по категориям с бар-чартом и долями."""
    if not totals:
        return []
    top = max(item.total for item in totals)
    name_width = min(14, max(len(item.name) for item in totals))
    lines: list[str] = []
    for item in totals:
        share = round(item.total / grand_total * 100) if grand_total else 0
        name = item.name if len(item.name) <= name_width else item.name[: name_width - 1] + "…"
        lines.append(
            f"{item.emoji} <code>{esc(name.ljust(name_width))}</code> "
            f"{bar(item.total, top, 8)} <b>{format_money(item.total, currency)}</b>"
            f" · {share}%"
        )
    return lines


async def transfers_line(
    db: Database, user: UserSettings, start: dt.date, end: dt.date
) -> Optional[str]:
    """Строка про долги и накопления — то, что не потрачено, а переложено.

    Отложенное вернётся, занятое придётся отдать. В общей сумме трат это
    искажает картину в обе стороны, поэтому считаем отдельно и говорим об этом
    вслух: молча выкинутые из сводки тысячи пугают сильнее, чем объяснённые.
    """
    out, out_count = await db.total_between(user.user_id, start, end, EXPENSE, transfers=True)
    into, in_count = await db.total_between(user.user_id, start, end, INCOME, transfers=True)
    if not out and not into:
        return None

    parts = []
    if out:
        parts.append(f"отложено и отдано <b>{format_money(out, user.currency)}</b>")
    if into:
        parts.append(f"занято и снято <b>{format_money(into, user.currency)}</b>")
    total = out_count + in_count
    return (
        f"🤝 Долги и накопления: {', '.join(parts)}"
        f" · {total} {records_word(total)}\n"
        "<i>В тратах и доходах выше это не учтено — деньги переложены, "
        "а не потрачены.</i>"
    )


def limit_line(spent: int, limit: int, currency: str) -> str:
    share = spent / limit if limit else 0
    if share >= 1:
        icon = "🔴"
    elif share >= 0.8:
        icon = "🟡"
    else:
        icon = "🟢"
    return (
        f"{icon} {format_money(spent, currency)} из {format_money(limit, currency)}"
        f" ({round(share * 100)}%) {bar(spent, limit, 10)}"
    )


async def build_report(
    db: Database, user: UserSettings, period: str, today: dt.date
) -> str:
    """Текст сводки по деньгам за выбранный период."""
    start, end, title = period_range(period, today)
    if period == "all":
        first = await db.first_transaction_date(user.user_id)
        start = first or today

    spent, spent_count = await db.total_between(
        user.user_id, start, end, EXPENSE, transfers=False
    )
    earned, earned_count = await db.total_between(
        user.user_id, start, end, INCOME, transfers=False
    )
    moved = await transfers_line(db, user, start, end)
    header = f"💰 <b>Деньги {esc(title)}</b>"

    if not spent and not earned and not moved:
        return (
            f"{header}\n\nПока пусто. Напиши, например: <code>кофе 300</code> "
            "или <code>+90000 зарплата</code>"
        )

    lines = [header]
    if earned:
        lines.append(
            f"Доходы: <b>{format_money(earned, user.currency)}</b> · "
            f"{earned_count} {records_word(earned_count)}"
        )
    lines.append(
        f"Расходы: <b>{format_money(spent, user.currency)}</b> · "
        f"{spent_count} {records_word(spent_count)}"
    )
    if earned:
        balance = earned - spent
        word = "Остаток" if balance >= 0 else "Перерасход"
        lines.append(f"{word}: <b>{format_money(abs(balance), user.currency)}</b>")
    lines.append("")

    if moved:
        lines.append(moved)
        lines.append("")

    if spent:
        totals = await db.totals_by_category(
            user.user_id, start, end, EXPENSE, transfers=False
        )
        lines.append("<b>Куда ушло</b>")
        lines.extend(render_breakdown(totals, spent, user.currency))

    if earned:
        income_totals = await db.totals_by_category(
            user.user_id, start, end, INCOME, transfers=False
        )
        if len(income_totals) > 1:
            lines.append("")
            lines.append("<b>Откуда пришло</b>")
            lines.extend(render_breakdown(income_totals, earned, user.currency))

    days = (end - start).days + 1
    if days > 1 and spent:
        lines.append("")
        lines.append(
            f"Средний день: <b>{format_money(round(spent / days), user.currency)}</b>"
        )
        if period == "month":
            days_in_month = (month_end(today) - month_start(today)).days + 1
            forecast = round(spent / days * days_in_month)
            lines.append(f"Прогноз на месяц: <b>{format_money(forecast, user.currency)}</b>")

    limit_block = await month_limit_summary(db, user, today)
    if limit_block:
        lines.append("")
        lines.append(limit_block)

    return "\n".join(lines)


async def month_limit_summary(
    db: Database, user: UserSettings, today: dt.date
) -> Optional[str]:
    limit = await db.get_limit(user.user_id, TOTAL_LIMIT_CATEGORY)
    if limit is None:
        return None
    spent, _ = await db.total_between(
        user.user_id, month_start(today), month_end(today), EXPENSE, transfers=False
    )
    left = limit - spent
    tail = (
        f"осталось {format_money(left, user.currency)}"
        if left >= 0
        else f"перерасход {format_money(-left, user.currency)}"
    )
    return f"🎯 Лимит на месяц: {limit_line(spent, limit, user.currency)}\n{tail}"


async def check_limits(
    db: Database, user: UserSettings, category_id: Optional[int], today: dt.date
) -> list[str]:
    """Предупреждения по лимитам после добавления расхода."""
    start, end = month_start(today), month_end(today)
    warnings: list[str] = []

    if category_id is not None:
        cat_limit = await db.get_limit(user.user_id, category_id)
        if cat_limit:
            spent = await db.category_total_between(user.user_id, category_id, start, end)
            category = await db.get_category(user.user_id, category_id)
            name = f"Лимит {category.title}" if category else "Лимит категории"
            warnings.extend(_limit_warning(name, spent, cat_limit, user.currency))

    total_limit = await db.get_limit(user.user_id, TOTAL_LIMIT_CATEGORY)
    if total_limit:
        spent, _ = await db.total_between(
            user.user_id, start, end, EXPENSE, transfers=False
        )
        warnings.extend(_limit_warning("Лимит на месяц", spent, total_limit, user.currency))

    return warnings


def _limit_warning(name: str, spent: int, limit: int, currency: str) -> list[str]:
    share = spent / limit if limit else 0
    if share >= 1:
        return [
            f"🔴 <b>{esc(name)}</b>: лимит {format_money(limit, currency)} превышен на "
            f"{format_money(spent - limit, currency)}"
        ]
    if share >= 0.8:
        return [
            f"🟡 <b>{esc(name)}</b>: потрачено {round(share * 100)}% лимита, "
            f"осталось {format_money(limit - spent, currency)}"
        ]
    return []


async def balance_text(db: Database, user: UserSettings, today: dt.date) -> str:
    """Короткий ответ на /balance: доходы, расходы и остаток за месяц."""
    start, end = month_start(today), month_end(today)
    earned, _ = await db.total_between(user.user_id, start, end, INCOME, transfers=False)
    spent, _ = await db.total_between(user.user_id, start, end, EXPENSE, transfers=False)
    moved = await transfers_line(db, user, start, end)
    balance = earned - spent

    if not earned and not spent and not moved:
        return f"💰 <b>{month_title(today)}</b>\n\nЗа этот месяц записей нет."

    sign = "🟢" if balance >= 0 else "🔴"
    word = "Остаток" if balance >= 0 else "Перерасход"
    text = (
        f"💰 <b>{month_title(today)}</b>\n\n"
        f"Пришло: <b>{format_money(earned, user.currency)}</b>\n"
        f"Ушло: <b>{format_money(spent, user.currency)}</b>\n"
        f"{sign} {word}: <b>{format_money(abs(balance), user.currency)}</b>"
    )
    return text + f"\n\n{moved}" if moved else text
