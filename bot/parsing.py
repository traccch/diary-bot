"""Разбор свободного текста вида «120/80 65 вчера 21:30 после прогулки».

Порядок слов не важен: сначала из строки вынимаются дата и время, потом
давление, потом пульс, а всё, что осталось, становится комментарием.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Optional

MIN_SYS, MAX_SYS = 60, 300
MIN_DIA, MAX_DIA = 30, 200
MIN_PULSE, MAX_PULSE = 25, 250

RELATIVE_DAYS: dict[str, int] = {
    "сегодня": 0,
    "вчера": 1,
    "позавчера": 2,
}

#: Слова-паразиты, которые не несут смысла в комментарии.
NOISE_WORDS = frozenset(
    {
        "давление",
        "ад",
        "тонометр",
        "измерил",
        "измерила",
        "померил",
        "померила",
        "замер",
        "пульс",
        "в",
        "на",
    }
)

_RELATIVE = re.compile(r"\b(" + "|".join(RELATIVE_DAYS) + r")\b", re.IGNORECASE)

_DATE = re.compile(r"(?<![\d.:])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d.:])")

_TIME = re.compile(r"(?<![\d.:])([01]?\d|2[0-3])[:.]([0-5]\d)(?![\d.:])")

# 120/80, 120\80, 120-80, «120 на 80»
_BP = re.compile(
    r"(?<!\d)(\d{2,3})\s*(?:/|\\|\||—|–|-|\bна\b)\s*(\d{2,3})(?!\d)", re.IGNORECASE
)
# «120 80» — два числа подряд через пробел
_BP_SPACED = re.compile(r"(?<!\d)(\d{2,3})\s+(\d{2,3})(?!\d)")

# «п72», «пульс 72», «чсс: 72»
_PULSE_MARKED = re.compile(
    r"\b(?:пульс|чсс|hr|п|p)(?![а-яёa-z])\s*[:=]?\s*(\d{2,3})(?!\d)", re.IGNORECASE
)
# число сразу после давления: «120/80 65», «120/80/65»
_PULSE_TRAILING = re.compile(r"^\s*[/\\,;-]?\s*(\d{2,3})(?!\d)")

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


class ParseError(ValueError):
    """Текст похож на измерение, но цифры не складываются в осмысленные."""


@dataclass(frozen=True)
class ParsedMeasurement:
    systolic: int
    diastolic: int
    pulse: Optional[int]
    measured_at: dt.datetime
    note: str


def _cut(text: str, start: int, end: int) -> str:
    return text[:start] + " " + text[end:]


def _cleanup_note(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" \t-–—:;,./\\ ")
    words = text.split()
    while words and words[0].lower().strip(".,:;") in NOISE_WORDS:
        words.pop(0)
    while words and words[-1].lower().strip(".,:;") in NOISE_WORDS:
        words.pop()
    return " ".join(words)


def _extract_date(text: str, today: dt.date) -> tuple[str, Optional[dt.date]]:
    """Ищет «вчера» или дату вида 15.08 / 15.08.2026."""
    match = _RELATIVE.search(text)
    if match is not None:
        delta = RELATIVE_DAYS[match.group(1).lower()]
        return _cut(text, match.start(), match.end()), today - dt.timedelta(days=delta)

    for match in _DATE.finditer(text):
        day, month = int(match.group(1)), int(match.group(2))
        raw_year = match.group(3)
        year = today.year if raw_year is None else int(raw_year)
        if raw_year is not None and year < 100:
            year += 2000
        try:
            parsed = dt.date(year, month, day)
        except ValueError:
            continue  # не дата (например, 8.30 — это время)
        if raw_year is None and parsed > today:
            parsed = dt.date(year - 1, month, day)
        return _cut(text, match.start(), match.end()), parsed
    return text, None


def _extract_time(text: str) -> tuple[str, Optional[dt.time]]:
    match = _TIME.search(text)
    if match is None:
        return text, None
    hour, minute = int(match.group(1)), int(match.group(2))
    return _cut(text, match.start(), match.end()), dt.time(hour, minute)


def _extract_pressure(text: str) -> tuple[str, Optional[tuple[int, int]], Optional[int]]:
    """Возвращает (остаток текста, (верхнее, нижнее), пульс-хвост)."""
    match = _BP.search(text) or _BP_SPACED.search(text)
    if match is None:
        return text, None, None

    systolic, diastolic = int(match.group(1)), int(match.group(2))
    tail = text[match.end() :]

    pulse = None
    trailing = _PULSE_TRAILING.match(tail)
    if trailing is not None:
        pulse = int(trailing.group(1))
        tail = tail[trailing.end() :]

    return text[: match.start()] + " " + tail, (systolic, diastolic), pulse


def _extract_marked_pulse(text: str) -> tuple[str, Optional[int]]:
    match = _PULSE_MARKED.search(text)
    if match is None:
        return text, None
    return _cut(text, match.start(), match.end()), int(match.group(1))


def _validate(systolic: int, diastolic: int, pulse: Optional[int]) -> None:
    if not MIN_SYS <= systolic <= MAX_SYS or not MIN_DIA <= diastolic <= MAX_DIA:
        raise ParseError(
            f"Такого давления не бывает: {systolic}/{diastolic}. "
            f"Верхнее — от {MIN_SYS} до {MAX_SYS}, нижнее — от {MIN_DIA} до {MAX_DIA}."
        )
    if systolic <= diastolic:
        raise ParseError(
            f"Верхнее должно быть больше нижнего, а получилось {systolic}/{diastolic}. "
            "Проверь, не перепутаны ли цифры."
        )
    if pulse is not None and not MIN_PULSE <= pulse <= MAX_PULSE:
        raise ParseError(
            f"Пульс {pulse} — похоже на опечатку, жду значение "
            f"от {MIN_PULSE} до {MAX_PULSE}."
        )


def parse_measurement(
    text: str, now: Optional[dt.datetime] = None
) -> Optional[ParsedMeasurement]:
    """Разбирает строку в измерение. None — если давления в тексте нет."""
    now = now or dt.datetime.now()
    working = (text or "").strip()
    if not working:
        return None

    working, date = _extract_date(working, now.date())
    working, time = _extract_time(working)
    working, pressure, pulse = _extract_pressure(working)
    if pressure is None:
        return None
    if pulse is None:
        working, pulse = _extract_marked_pulse(working)

    systolic, diastolic = pressure
    _validate(systolic, diastolic, pulse)

    measured_at = dt.datetime.combine(date or now.date(), time or now.time().replace(second=0))
    return ParsedMeasurement(
        systolic=systolic,
        diastolic=diastolic,
        pulse=pulse,
        measured_at=measured_at.replace(second=0, microsecond=0),
        note=_cleanup_note(working),
    )


def parse_time(text: str) -> Optional[dt.time]:
    """Время для напоминаний: «8:00», «8.00», «800», «8»."""
    raw = (text or "").strip()
    if not raw:
        return None

    match = re.fullmatch(r"(\d{1,2})\s*[:.\-]\s*(\d{2})", raw)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
    elif re.fullmatch(r"\d{3,4}", raw):
        hour, minute = int(raw[:-2]), int(raw[-2:])
    elif re.fullmatch(r"\d{1,2}", raw):
        hour, minute = int(raw), 0
    else:
        return None

    if hour > 23 or minute > 59:
        return None
    return dt.time(hour, minute)


def looks_like_numbers(text: str) -> bool:
    """Есть ли в тексте вообще цифры — чтобы отличить «привет» от «12080»."""
    return any(char.isdigit() for char in text or "")


def iter_words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())
