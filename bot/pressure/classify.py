"""Классификация давления по шкале ESC/ESH (Европейское общество кардиологов).

Категория определяется по «худшему» из двух значений: 145/85 — это уже
АГ 1 степени, хотя нижнее в норме. Отдельно выделены изолированная
систолическая гипертензия и пониженное давление.

Важно: шкала ниже — для измерений на приёме у врача. Дома пороги ниже,
гипертонией считают средние значения от 135/85 — поэтому «целевые» значения
пользователя (см. /target) по умолчанию 135/85, а не 140/90.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Давление, при котором нельзя отмалчиваться — нужен врач, а не бот.
CRISIS_SYS = 180
CRISIS_DIA = 120
#: Слишком низкое: обморочные значения.
LOW_SYS = 85
LOW_DIA = 50


@dataclass(frozen=True)
class Grade:
    key: str
    title: str
    short: str
    icon: str
    order: int


HYPOTENSION = Grade("hypotension", "Пониженное", "низкое", "🔵", 0)
OPTIMAL = Grade("optimal", "Оптимальное", "оптим.", "🟢", 1)
NORMAL = Grade("normal", "Нормальное", "норма", "🟢", 2)
HIGH_NORMAL = Grade("high_normal", "Высокое нормальное", "выс. норма", "🟡", 3)
ISOLATED_SYSTOLIC = Grade("ish", "Изолированная систолическая АГ", "ИСАГ", "🟠", 4)
GRADE_1 = Grade("ag1", "АГ 1 степени", "АГ 1", "🟠", 5)
GRADE_2 = Grade("ag2", "АГ 2 степени", "АГ 2", "🔴", 6)
GRADE_3 = Grade("ag3", "АГ 3 степени", "АГ 3", "🔴", 7)

ALL_GRADES: tuple[Grade, ...] = (
    HYPOTENSION,
    OPTIMAL,
    NORMAL,
    HIGH_NORMAL,
    ISOLATED_SYSTOLIC,
    GRADE_1,
    GRADE_2,
    GRADE_3,
)


def classify(systolic: int, diastolic: int) -> Grade:
    """Категория давления по шкале ESC/ESH 2018."""
    if systolic >= 140 and diastolic < 90:
        return ISOLATED_SYSTOLIC
    if systolic >= 180 or diastolic >= 110:
        return GRADE_3
    if systolic >= 160 or diastolic >= 100:
        return GRADE_2
    if systolic >= 140 or diastolic >= 90:
        return GRADE_1
    if systolic >= 130 or diastolic >= 85:
        return HIGH_NORMAL
    if systolic < 90 or diastolic < 60:
        return HYPOTENSION
    if systolic >= 120 or diastolic >= 80:
        return NORMAL
    return OPTIMAL


def alert(systolic: int, diastolic: int, pulse: int | None = None) -> str | None:
    """Предупреждение, если цифры требуют не заметки в дневнике, а действий."""
    if systolic >= CRISIS_SYS or diastolic >= CRISIS_DIA:
        return (
            "‼️ Очень высокое давление. Посиди спокойно 5 минут и перемерь. "
            "Если цифры держатся, а тем более если болит голова или грудь, "
            "тяжело дышать, немеет рука — вызывай скорую (103), не жди."
        )
    if systolic <= LOW_SYS or diastolic <= LOW_DIA:
        return (
            "⚠️ Давление низкое. Если кружится голова или темнеет в глазах — "
            "лучше лечь и поднять ноги, а потом перемерить."
        )
    if pulse is not None and pulse >= 120:
        return "⚠️ Высокий пульс. Перемерь в покое через 5 минут, не после нагрузки."
    if pulse is not None and pulse <= 45:
        return "⚠️ Редкий пульс. Если есть слабость или головокружение — покажись врачу."
    return None


def in_target(systolic: int, diastolic: int, target_sys: int, target_dia: int) -> bool:
    return systolic < target_sys and diastolic < target_dia
