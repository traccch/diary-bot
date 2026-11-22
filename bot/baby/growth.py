"""Рост, вес и окружность головы: разбор записи и сравнение с нормами ВОЗ.

Сравнение — по методу LMS, которым пользуется сама ВОЗ: по возрасту в днях
берутся коэффициенты L, M, S, из них — z-оценка (на сколько стандартных
отклонений ребёнок от медианы) и перцентиль. Коридор от −2 до +2 — это
середина, куда попадают 95 % здоровых детей; выход за него — повод спросить
педиатра, а не диагноз.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from typing import Optional

from . import who

WEIGHT = "weight"
LENGTH = "length"
HEAD = "head"

TITLES = {WEIGHT: "Вес", LENGTH: "Рост", HEAD: "Голова"}
UNITS = {WEIGHT: "кг", LENGTH: "см", HEAD: "см"}

#: Правдоподобные значения до трёх лет: всё, что за пределами, — опечатка.
SANE = {WEIGHT: (1.5, 25.0), LENGTH: (40.0, 110.0), HEAD: (30.0, 55.0)}

_NUMBER = r"(\d{1,5}(?:[.,]\d{1,2})?)"
_PATTERNS = {
    LENGTH: re.compile(rf"\b(?:рост|длина)\s*[:=]?\s*{_NUMBER}", re.IGNORECASE),
    WEIGHT: re.compile(rf"\bвес\s*[:=]?\s*{_NUMBER}\s*(кг|гр|г)?\b", re.IGNORECASE),
    HEAD: re.compile(
        rf"\b(?:голова|окружность\s+головы|огол|ог)\s*[:=]?\s*{_NUMBER}", re.IGNORECASE
    ),
}
_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b")


@dataclass(frozen=True)
class Entry:
    on: dt.date
    values: dict  # показатель -> число


def _number(raw: str) -> float:
    return float(raw.replace(",", "."))


def parse(text: str, today: dt.date) -> Optional[Entry]:
    """«рост 76 вес 9,4 голова 45 15.11» → Entry. None — это не замеры."""
    values: dict[str, float] = {}
    rest = text
    for key, pattern in _PATTERNS.items():
        match = pattern.search(rest)
        if not match:
            continue
        value = _number(match.group(1))
        if key == WEIGHT and (match.group(2) in ("г", "гр") or value > 100):
            value = value / 1000  # «вес 9400 г»
        low, high = SANE[key]
        if not low <= value <= high:
            return None
        values[key] = round(value, 2)
        rest = rest[: match.start()] + " " + rest[match.end():]
    if not values:
        return None

    on = today
    found = _DATE.search(rest)
    if found:
        day, month = int(found.group(1)), int(found.group(2))
        year = found.group(3)
        year_value = today.year if not year else (int(year) + 2000 if len(year) == 2 else int(year))
        try:
            on = dt.date(year_value, month, day)
        except ValueError:
            return None
        if on > today:
            on = on.replace(year=on.year - 1)
    return Entry(on=on, values=values)


def _lms(kind: str, sex: str, day: int) -> Optional[tuple[float, float, float]]:
    table = who.TABLES.get((kind, sex))
    if not table or day < 0 or day > who.MAX_DAY:
        return None
    index = min(day // who.STEP_DAYS, len(table) - 2)
    left, right = table[index], table[index + 1]
    share = (day - left[0]) / (right[0] - left[0])
    return tuple(a + (b - a) * share for a, b in zip(left[1:], right[1:]))  # type: ignore[return-value]


def zscore(kind: str, sex: str, day: int, value: float) -> Optional[float]:
    lms = _lms(kind, sex, day)
    if lms is None or value <= 0:
        return None
    l, m, s = lms
    if abs(l) < 1e-9:
        return math.log(value / m) / s
    return ((value / m) ** l - 1) / (l * s)


def median(kind: str, sex: str, day: int) -> Optional[float]:
    lms = _lms(kind, sex, day)
    return lms[1] if lms else None


def at_z(kind: str, sex: str, day: int, z: float) -> Optional[float]:
    """Значение показателя на заданной z-оценке — для коридора на графике."""
    lms = _lms(kind, sex, day)
    if lms is None:
        return None
    l, m, s = lms
    if abs(l) < 1e-9:
        return m * math.exp(s * z)
    return m * (1 + l * s * z) ** (1 / l)


def percentile(z: float) -> float:
    return 50 * (1 + math.erf(z / math.sqrt(2)))


def assess(z: float) -> str:
    if abs(z) <= 2:
        return "в пределах нормы"
    if abs(z) <= 3:
        return "у края нормы — стоит обсудить с педиатром"
    return "заметно вне нормы — покажите педиатру"


def describe(kind: str, sex: str, birth: dt.date, on: dt.date, value: float) -> str:
    """Строка «Вес 9,4 кг — 52-й перцентиль, в пределах нормы (медиана 9,4)»."""
    day = (on - birth).days
    unit = UNITS[kind]
    shown = f"{value:g}".replace(".", ",")
    z = zscore(kind, sex, day, value)
    if z is None:
        return f"{TITLES[kind]} {shown} {unit}"
    middle = f"{median(kind, sex, day):.1f}".replace(".", ",")
    pct = percentile(z)
    pct_text = "<1" if pct < 1 else ">99" if pct > 99 else f"{pct:.0f}"
    return (
        f"{TITLES[kind]} <b>{shown} {unit}</b> — {pct_text}-й перцентиль, {assess(z)}"
        f" <i>(середина нормы: {middle})</i>"
    )
