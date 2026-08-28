"""ТО по пробегу: когда спросить, когда напомнить и когда молчать.

Напоминание о ТО раз в день бесполезно: до него десять тысяч километров, и
человек привыкает пролистывать. Поэтому здесь всего два состояния, при
которых бот вообще открывает рот, — «подходит» и «просрочено». Всё остальное
время про ТО не слышно, хотя оно посчитано.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from .db import Service
from .stats import format_km

#: За сколько километров начинаем предупреждать.
WARN_KM = 1000

#: Через столько дней спросим про ТО ещё раз, если промолчал.
ASK_AGAIN_DAYS = 7
#: А если сказал «не веду» — не раньше чем через полгода.
MUTED_DAYS = 180

#: Обычные интервалы, из которых удобно выбрать кнопкой.
INTERVALS = (5000, 10000, 15000)

FAR = "far"
SOON = "soon"
OVERDUE = "overdue"


def state(service: Optional[Service], km: int) -> str:
    """До ТО далеко, близко или оно уже просрочено."""
    if service is None:
        return FAR
    left = service.left(km)
    if left < 0:
        return OVERDUE
    return SOON if left <= WARN_KM else FAR


def line(service: Optional[Service], km: int) -> str:
    """Строка про ТО — только когда есть что сказать."""
    if service is None:
        return ""
    left = service.left(km)
    if left < 0:
        return (
            f"🔧 <b>ТО просрочено</b> на {format_km(-left)} км "
            f"(было назначено на {format_km(service.due_km)})"
        )
    if left <= WARN_KM:
        return f"🔧 До ТО осталось <b>{format_km(left)} км</b>"
    return ""


def full_line(service: Optional[Service], km: int) -> str:
    """То же для /car, где уместно сказать и когда до ТО далеко."""
    if service is None:
        return "🔧 ТО: не назначено"
    said = line(service, km)
    if said:
        return said
    return (
        f"🔧 ТО на {format_km(service.due_km)} км · "
        f"осталось {format_km(service.left(km))} км"
    )


def targets(km: int) -> list[tuple[int, int]]:
    """Кнопки «через сколько»: (интервал, круглый пробег ТО)."""
    return [(step, round((km + step) / 500) * 500) for step in INTERVALS]


def next_after_done(service: Service, km: int) -> Optional[int]:
    """Следующее ТО после отметки «сделал» — если интервал известен."""
    interval = service.interval_km
    return km + interval if interval > 0 else None


def ask_again(today: dt.date, muted: bool = False) -> dt.date:
    return today + dt.timedelta(days=MUTED_DAYS if muted else ASK_AGAIN_DAYS)
