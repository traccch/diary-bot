"""Что видно из показаний одометра: сколько проехал и во что это обошлось."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional, Sequence

from ..formatting import format_money, plural
from ..money.db import EXPENSE
from .db import Fuel, Reading

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
    fuel = await fuel_lines(db, user, month_start, today)
    if cost or fuel:
        lines.append("")
    if cost:
        lines.append(cost)
    lines.extend(fuel)

    from .service import full_line

    lines.append(full_line(await db.get_service(user.user_id), last.km))

    if last.on_date != today:
        lines.append("")
        lines.append("<i>Сегодняшнего показания нет — пришли число с одометра.</i>")
    return "\n".join(lines)


async def fuel_lines(db, user, start: dt.date, end: dt.date) -> list[str]:
    """Заправки за месяц и, если хватает данных, расход на сотню.

    Расход считается по-честному: литры, залитые между двумя заправками,
    на километры, проеханные за то же время. Первая заправка в счёт не идёт —
    неизвестно, сколько было в баке до неё.
    """
    fills = await db.fuel_between(user.user_id, start, end)
    if not fills:
        return []

    litres = sum(item.litres for item in fills)
    spent = sum(item.amount for item in fills)
    word = plural(len(fills), "заправка", "заправки", "заправок")
    line = f"⛽ {len(fills)} {word} · {litres:.1f} л".replace(".", ",")
    if spent and litres:
        line += f" · {format_money(round(spent / litres), user.currency)} за литр"
    lines = [line]

    per_hundred = await consumption(db, user.user_id, fills)
    if per_hundred:
        lines.append(f"Расход: <b>{per_hundred:.1f} л на 100 км</b>".replace(".", ","))
    return lines


#: За этими границами это не расход, а пропущенная заправка или опечатка.
MIN_CONSUMPTION, MAX_CONSUMPTION = 2.0, 30.0


async def consumption(db, user_id: int, fills: Sequence[Fuel]) -> Optional[float]:
    """Литров на сотню: залитое между заправками на проеханное за то же время.

    Считается только по записанным заправкам, а заправку легко забыть —
    заплатил наличными и не отметил. Тогда литры делятся на вдвое большее
    расстояние, и получается литр на сотню. Такой ответ хуже, чем никакого,
    поэтому неправдоподобное не показываем вовсе.
    """
    spent_litres, driven = 0.0, 0
    for previous, current in zip(fills, fills[1:]):
        start_km = await _km_at(db, user_id, previous.on_date)
        end_km = await _km_at(db, user_id, current.on_date)
        if start_km is None or end_km is None or end_km <= start_km:
            continue
        spent_litres += current.litres
        driven += end_km - start_km
    if not spent_litres or not driven:
        return None
    per_hundred = spent_litres / driven * 100
    if not MIN_CONSUMPTION <= per_hundred <= MAX_CONSUMPTION:
        return None
    return per_hundred


async def _km_at(db, user_id: int, day: dt.date) -> Optional[int]:
    """Пробег на этот день: точное показание или ближайшее до него."""
    exact = await db.reading_on(user_id, day)
    if exact is not None:
        return exact.km
    before = await db.reading_before(user_id, day)
    return before.km if before else None


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


def _litres(value: float) -> str:
    return f"{value:.1f} л".replace(".", ",")


async def build_fuel_report(db, user, today: dt.date) -> str:
    """Отдельный разговор про топливо: сколько, почём и как менялось."""
    everything = await db.fuel_between(user.user_id, dt.date(1970, 1, 1), today)
    if not everything:
        return (
            "⛽ <b>Топливо</b>\n\nЗаправок пока нет.\n"
            "Пиши как обычно: <code>-833 заправка 13,2 л</code> — литры я запомню "
            "и посчитаю расход."
        )

    month_start = today.replace(day=1)
    month = [item for item in everything if item.on_date >= month_start]

    lines = ["⛽ <b>Топливо</b>", ""]
    for title, fills in (("За месяц", month), ("За всё время", everything)):
        if not fills:
            continue
        litres = sum(item.litres for item in fills)
        spent = sum(item.amount for item in fills)
        word = plural(len(fills), "заправка", "заправки", "заправок")
        line = f"{title}: <b>{len(fills)} {word}</b> · {_litres(litres)}"
        if spent:
            line += f" · {format_money(spent, user.currency)}"
        lines.append(line)

    priced = [item for item in everything if item.amount and item.litres]
    if priced:
        average = round(sum(item.amount for item in priced) / sum(item.litres for item in priced))
        lines.append("")
        lines.append(f"Литр в среднем: <b>{format_money(average, user.currency)}</b>")

        cheapest = min(priced, key=lambda item: item.price_per_litre)
        dearest = max(priced, key=lambda item: item.price_per_litre)
        if cheapest is not dearest:
            lines.append(
                f"Дешевле всего {format_money(cheapest.price_per_litre, user.currency)}"
                f" ({cheapest.on_date:%d.%m}), дороже —"
                f" {format_money(dearest.price_per_litre, user.currency)}"
                f" ({dearest.on_date:%d.%m})"
            )
        if len(priced) > 1:
            first, last = priced[0].price_per_litre, priced[-1].price_per_litre
            if first and first != last:
                change = round((last - first) / first * 100)
                sign = "+" if change > 0 else ""
                lines.append(
                    f"С первой заправки: {format_money(first, user.currency)} → "
                    f"{format_money(last, user.currency)} ({sign}{change}%)"
                )

    per_hundred = await consumption(db, user.user_id, everything)
    if per_hundred:
        lines.append("")
        lines.append(f"Расход: <b>{per_hundred:.1f} л на 100 км</b>".replace(".", ","))
        litre_km = 100 / per_hundred
        lines.append(f"На литре проезжаешь {litre_km:.1f} км".replace(".", ","))
    elif len(everything) < 2:
        lines.append("")
        lines.append(
            "<i>Расход посчитаю со второй заправки: неизвестно, сколько было "
            "в баке до первой.</i>"
        )
    else:
        lines.append("")
        lines.append(
            "<i>Расход не считаю: цифры выходят неправдоподобные. Обычно так "
            "бывает, когда какая-то заправка не записана или пропущены "
            "показания одометра.</i>"
        )
    return "\n".join(lines)
