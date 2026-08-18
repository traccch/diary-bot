"""Показатели здоровья рядом с давлением: сон, шаги, пульс покоя, вес.

Каждый показатель — одно значение на день. Смысл их собирать не в самих
цифрах, а в связке с давлением: короткий сон и малоподвижные дни видно
в сводке рядом с утренними цифрами.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class MetricKind:
    key: str
    title: str
    icon: str
    #: «bars» — столбики по дням (сон, шаги), «line» — линия (вес, пульс)
    chart: str
    #: допустимый диапазон значений — за ним это опечатка
    low: float
    high: float
    axis: str


SLEEP = MetricKind("sleep", "Сон", "😴", "bars", 30, 16 * 60, "часов")
STEPS = MetricKind("steps", "Шаги", "👟", "bars", 0, 120_000, "шагов")
RESTING_PULSE = MetricKind("resting_pulse", "Пульс покоя", "💓", "line", 25, 150, "уд/мин")
WEIGHT = MetricKind("weight", "Вес", "⚖️", "line", 20, 400, "кг")

ALL_KINDS: tuple[MetricKind, ...] = (SLEEP, STEPS, RESTING_PULSE, WEIGHT)
BY_KEY = {kind.key: kind for kind in ALL_KINDS}


def kind_of(key: str) -> Optional[MetricKind]:
    return BY_KEY.get(key)


def hours_and_minutes(minutes: float) -> tuple[int, int]:
    total = int(round(minutes))
    return divmod(total, 60)


def format_value(key: str, value: float, short: bool = False) -> str:
    """Человеческое представление: 460 → «7 ч 40 мин», 8200 → «8 200 шагов»."""
    if key == SLEEP.key:
        hours, minutes = hours_and_minutes(value)
        if short:
            return f"{hours}:{minutes:02d}"
        return f"{hours} ч {minutes:02d} мин" if minutes else f"{hours} ч"
    if key == STEPS.key:
        grouped = f"{int(round(value)):,}".replace(",", " ")
        return grouped if short else f"{grouped} шагов"
    if key == RESTING_PULSE.key:
        return f"{int(round(value))}" if short else f"{int(round(value))} уд/мин"
    if key == WEIGHT.key:
        text = f"{value:.1f}".replace(".", ",").removesuffix(",0")
        return text if short else f"{text} кг"
    return str(value)


def chart_value(key: str, value: float) -> float:
    """Значение в единицах оси графика: сон в минутах хранится, а рисуется в часах."""
    return value / 60 if key == SLEEP.key else value


def median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
