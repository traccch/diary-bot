"""Что видно из показаний одометра: сколько проехал и во что это обошлось."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional, Sequence

from ..formatting import format_money, plural
from ..money.db import EXPENSE
from .db import Reading

#: Категория, по которой считаем стоимость километра.
FUEL_CATEGORY = "Транспорт"


def km_word(count: int) -> str:
    return plural(count, "километр", "километра", "километров")


def format_km(km: int) -> str:
    return f"{int(km):,}".replace(",", " ")


@dataclass(frozen=True)
class Ride:
    """Пробег за период: сколько накатано и за сколько дней."""

    driven: int
    days: int

    @property
    def per_day(self) -> int:
        return round(self.driven / self.days) if self.days else 0


def ride_between(
    readings: Sequence[Reading], before: Optional[Reading]
) -> Optional[Ride]:
    """Сколько проехал: от последнего показания до периода — к последнему в нём."""
    if not readings:
        return None
    start = before or readings[0]
    finish = readings[-1]
    days = (finish.on_date - start.on_date).days
    if days <= 0:
        return None
    return Ride(max(0, finish.km - start.km), days)


async def build_report(db, user, today: dt.date) -> str:
    """Текст /car: текущий пробег, сколько накатано и цена километра."""
    last = await db.last_reading(user.user_id)
    if last is None:
        return (
            "🚗 <b>Пробег</b>\n\nПоказаний ещё нет.\n"
            "Пришли число с одометра: <code>пробег 203116</code>.\n\n"
            "<i>Каждое утро буду напоминать — по разнице видно, "
            "сколько выходит в день и во что обходится километр.</i>"
        )

    lines = [
        "🚗 <b>Пробег</b>",
        f"На одометре: <b>{format_km(last.km)} км</b>"
        + (" · сегодня" if last.on_date == today else f" · {last.on_date:%d.%m}"),
        "",
    ]

    month_start = today.replace(day=1)
    for title, start in (("За неделю", today - dt.timedelta(days=6)), ("За месяц", month_start)):
        readings = await db.readings_between(user.user_id, start, today)
        ride = ride_between(readings, await db.reading_before(user.user_id, start))
        if ride is None:
            continue
        lines.append(
            f"{title}: <b>{format_km(ride.driven)} км</b>"
            f" · {format_km(ride.per_day)} км в день"
        )

    cost = await cost_per_km(db, user, month_start, today)
    if cost:
        lines.append("")
        lines.append(cost)

    if last.on_date != today:
        lines.append("")
        lines.append("<i>Сегодняшнего показания нет — пришли число с одометра.</i>")
    return "\n".join(lines)


async def cost_per_km(db, user, start: dt.date, end: dt.date) -> str:
    """Во что обошёлся километр: траты на транспорт делим на накатанное."""
    readings = await db.readings_between(user.user_id, start, end)
    ride = ride_between(readings, await db.reading_before(user.user_id, start))
    if ride is None or ride.driven <= 0:
        return ""

    category = await db.find_category_by_name(user.user_id, FUEL_CATEGORY, EXPENSE)
    if category is None:
        return ""
    spent = await db.category_total_between(user.user_id, category.id, start, end)
    if not spent:
        return ""

    per_km = round(spent / ride.driven)
    return (
        f"⛽ Транспорт за месяц: <b>{format_money(spent, user.currency)}</b>"
        f" · {format_money(per_km, user.currency)} за км"
    )
