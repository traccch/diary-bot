"""Ориентиры по сну малыша в зависимости от возраста.

Общий сон за сутки — по консенсусу Американской академии медицины сна
(AASM, 2016): 4–12 месяцев — 12–16 часов, 1–2 года — 11–14 часов; для
младенцев до 4 месяцев — по рекомендациям National Sleep Foundation
(14–17 часов). Окна бодрствования и число дневных снов — распространённые
педиатрические ориентиры, а не строгие нормы: у каждого ребёнка свой ритм.
Бот подсказывает, но не ставит диагнозов.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class SleepNorm:
    months_from: int
    naps: tuple[int, int]  # сколько дневных снов
    wake_hours: tuple[float, float]  # окно бодрствования
    day_hours: tuple[float, float]  # дневной сон всего
    night_hours: tuple[float, float]  # ночной сон
    total_hours: tuple[float, float]  # за сутки

    @property
    def naps_text(self) -> str:
        low, high = self.naps
        return f"{low}" if low == high else f"{low}–{high}"


#: По возрастам, от младших к старшим: берётся последняя подходящая строка.
NORMS: tuple[SleepNorm, ...] = (
    SleepNorm(0, (4, 6), (0.75, 1.0), (4, 8), (8, 10), (14, 17)),
    SleepNorm(2, (4, 5), (1.0, 1.75), (4, 6), (9, 11), (14, 17)),
    SleepNorm(4, (3, 4), (1.75, 2.5), (3, 5), (10, 12), (12, 16)),
    SleepNorm(6, (2, 3), (2.5, 3.5), (2.5, 4), (10, 12), (12, 16)),
    SleepNorm(9, (2, 2), (3.0, 4.0), (2, 3), (10, 12), (12, 16)),
    SleepNorm(13, (1, 2), (4.0, 5.5), (1.5, 2.5), (10, 12), (11, 14)),
    SleepNorm(19, (1, 1), (5.0, 6.0), (1, 2), (10, 12), (11, 14)),
)


def age_months(birth: dt.date, today: dt.date) -> int:
    """Полных месяцев жизни."""
    months = (today.year - birth.year) * 12 + today.month - birth.month
    if today.day < birth.day:
        months -= 1
    return max(0, months)


def age_text(birth: dt.date, today: dt.date) -> str:
    months = age_months(birth, today)
    if months < 1:
        days = (today - birth).days
        return f"{days} дн."
    if months < 24:
        return f"{months} мес."
    years, rest = divmod(months, 12)
    return f"{years} г. {rest} мес." if rest else f"{years} г."


def norm_for(months: int) -> SleepNorm:
    found = NORMS[0]
    for norm in NORMS:
        if months >= norm.months_from:
            found = norm
    return found


def hours_text(low: float, high: float) -> str:
    def one(value: float) -> str:
        whole = int(value)
        minutes = round((value - whole) * 60)
        if not minutes:
            return f"{whole}"
        if whole == 0:
            return f"{minutes} мин"
        return f"{whole} ч {minutes} мин"

    left, right = one(low), one(high)
    if "мин" in right:
        return f"{left}–{right}"
    return f"{left}–{right} ч"


def verdict(value_hours: float, band: tuple[float, float]) -> str:
    """Короткая оценка рядом с цифрой: в норме, маловато, многовато."""
    low, high = band
    if value_hours < low * 0.9:
        return "маловато"
    if value_hours > high * 1.1:
        return "многовато"
    return "в норме"


def bedtime_window(
    awake_since: dt.datetime, norm: SleepNorm
) -> tuple[dt.datetime, dt.datetime]:
    """Когда пора укладывать: начало и конец окна бодрствования."""
    low, high = norm.wake_hours
    return (
        awake_since + dt.timedelta(hours=low),
        awake_since + dt.timedelta(hours=high),
    )


def duration_text(delta: dt.timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} ч {minutes:02d} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


def as_hours(delta: dt.timedelta) -> float:
    return delta.total_seconds() / 3600


def describe_hours(value: float) -> str:
    return duration_text(dt.timedelta(hours=value))


__all__ = [
    "SleepNorm",
    "NORMS",
    "age_months",
    "age_text",
    "norm_for",
    "hours_text",
    "verdict",
    "bedtime_window",
    "duration_text",
    "as_hours",
    "describe_hours",
]
