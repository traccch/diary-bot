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

# ---------------------------------------------------------- показатели здоровья

_SLEEP_WORD = r"(?:сон|сна|спал[аи]?|поспал[аи]?)"

# «сон 23:21-7:01», «спал с 23:21 до 7:01»
_SLEEP_RANGE = re.compile(
    _SLEEP_WORD + r"\b[\s:]*(?:с\s+)?(\d{1,2})[:.](\d{2})\s*(?:-|–|—|до|по)\s*"
    r"(\d{1,2})[:.](\d{2})",
    re.IGNORECASE,
)
# «сон 7ч40м», «спал 7 часов», «сон 7 ч».
# Варианты слов идут от длинного к короткому: иначе «ч» съест начало «часов».
_SLEEP_HOURS = re.compile(
    _SLEEP_WORD + r"\b[\s:]*(\d{1,2})\s*(?:часов|часа|час|ч)\.?"
    r"(?:\s*(\d{1,2})\s*(?:минут[аы]?|минут|мин|м)?\.?)?",
    re.IGNORECASE,
)
# «сон 7:40» — одно время после слова «сон» читаем как длительность
_SLEEP_CLOCK = re.compile(_SLEEP_WORD + r"\b[\s:]*(\d{1,2})[:.](\d{2})", re.IGNORECASE)

_STEPS = re.compile(
    r"(?:шаг(?:и|ов)?\b[\s:]*(\d{3,6})|(\d{3,6})\s*шаг(?:ов|а|и)?\b)", re.IGNORECASE
)

# «пульс покоя 58», «пп 58», «rhr 58» — ищется до обычного пульса
_RESTING_PULSE = re.compile(
    r"(?:пульс\s+(?:покоя|в\s+покое)|пп|rhr)\b[\s:=]*(\d{2,3})", re.IGNORECASE
)

_WEIGHT = re.compile(
    r"(?:вес\b[\s:=]*(\d{2,3}(?:[.,]\d{1,2})?)|(\d{2,3}(?:[.,]\d{1,2})?)\s*кг\b)",
    re.IGNORECASE,
)

MIN_SLEEP_MINUTES, MAX_SLEEP_MINUTES = 30, 16 * 60
MIN_STEPS, MAX_STEPS = 0, 120_000
MIN_RESTING_PULSE, MAX_RESTING_PULSE = 25, 150
MIN_WEIGHT, MAX_WEIGHT = 20.0, 400.0


class ParseError(ValueError):
    """Текст похож на измерение, но цифры не складываются в осмысленные."""


@dataclass(frozen=True)
class ParsedMetric:
    kind: str
    value: float
    extra: str = ""


@dataclass(frozen=True)
class ParsedMeasurement:
    systolic: int
    diastolic: int
    pulse: Optional[int]
    measured_at: dt.datetime
    note: str


@dataclass(frozen=True)
class ParsedEntry:
    """Что удалось вычитать из сообщения: измерение, показатели, комментарий."""

    measurement: Optional[ParsedMeasurement]
    metrics: tuple[ParsedMetric, ...]
    on_date: dt.date
    note: str

    def __bool__(self) -> bool:
        return self.measurement is not None or bool(self.metrics)


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


def _extract_metrics(text: str) -> tuple[str, list[ParsedMetric]]:
    """Вынимает сон, шаги, пульс покоя и вес. Порядок важен.

    Сон ищется раньше времени замера, иначе «23:21-7:01» станет временем;
    пульс покоя — раньше обычного пульса, иначе «пульс покоя 58» съест его.
    """
    found: list[ParsedMetric] = []

    match = _SLEEP_RANGE.search(text)
    if match is not None:
        bed = dt.time(int(match.group(1)) % 24, int(match.group(2)))
        wake = dt.time(int(match.group(3)) % 24, int(match.group(4)))
        minutes = (
            dt.datetime.combine(dt.date(2000, 1, 2), wake)
            - dt.datetime.combine(dt.date(2000, 1, 1), bed)
        ).total_seconds() / 60
        if minutes > 24 * 60:
            minutes -= 24 * 60  # лёг после полуночи: 01:00–07:00 — это 6 часов
        _check_sleep(minutes)
        found.append(
            ParsedMetric(
                "sleep", minutes, f"{bed.strftime('%H:%M')}-{wake.strftime('%H:%M')}"
            )
        )
        text = _cut(text, match.start(), match.end())
    else:
        match = _SLEEP_HOURS.search(text)
        if match is not None:
            minutes = int(match.group(1)) * 60 + int(match.group(2) or 0)
            _check_sleep(minutes)
            found.append(ParsedMetric("sleep", minutes))
            text = _cut(text, match.start(), match.end())
        else:
            match = _SLEEP_CLOCK.search(text)
            if match is not None:
                minutes = int(match.group(1)) * 60 + int(match.group(2))
                _check_sleep(minutes)
                found.append(ParsedMetric("sleep", minutes))
                text = _cut(text, match.start(), match.end())

    match = _STEPS.search(text)
    if match is not None:
        steps = int(match.group(1) or match.group(2))
        if not MIN_STEPS <= steps <= MAX_STEPS:
            raise ParseError(f"{steps} шагов — похоже на опечатку.")
        found.append(ParsedMetric("steps", steps))
        text = _cut(text, match.start(), match.end())

    match = _RESTING_PULSE.search(text)
    if match is not None:
        pulse = int(match.group(1))
        if not MIN_RESTING_PULSE <= pulse <= MAX_RESTING_PULSE:
            raise ParseError(
                f"Пульс покоя {pulse} — жду значение от {MIN_RESTING_PULSE} "
                f"до {MAX_RESTING_PULSE}."
            )
        found.append(ParsedMetric("resting_pulse", pulse))
        text = _cut(text, match.start(), match.end())

    match = _WEIGHT.search(text)
    if match is not None:
        weight = float((match.group(1) or match.group(2)).replace(",", "."))
        if not MIN_WEIGHT <= weight <= MAX_WEIGHT:
            raise ParseError(
                f"Вес {weight} кг — жду значение от {MIN_WEIGHT:.0f} до {MAX_WEIGHT:.0f}."
            )
        found.append(ParsedMetric("weight", weight))
        text = _cut(text, match.start(), match.end())

    return text, found


def _check_sleep(minutes: float) -> None:
    if not MIN_SLEEP_MINUTES <= minutes <= MAX_SLEEP_MINUTES:
        hours = minutes / 60
        raise ParseError(
            f"Сон {hours:.1f} ч — похоже на опечатку. Напиши так: "
            "<code>сон 23:21-7:01</code> или <code>сон 7ч40м</code>."
        )


def parse_entry(text: str, now: Optional[dt.datetime] = None) -> ParsedEntry:
    """Разбирает сообщение целиком: давление, показатели здоровья, комментарий."""
    now = now or dt.datetime.now()
    working = (text or "").strip()
    if not working:
        return ParsedEntry(None, (), now.date(), "")

    working, date = _extract_date(working, now.date())
    working, metrics = _extract_metrics(working)
    working, time = _extract_time(working)
    working, pressure, pulse = _extract_pressure(working)

    if pressure is None:
        return ParsedEntry(None, tuple(metrics), date or now.date(), _cleanup_note(working))

    if pulse is None:
        working, pulse = _extract_marked_pulse(working)

    systolic, diastolic = pressure
    _validate(systolic, diastolic, pulse)

    measured_at = dt.datetime.combine(date or now.date(), time or now.time())
    measured_at = measured_at.replace(second=0, microsecond=0)
    note = _cleanup_note(working)
    return ParsedEntry(
        measurement=ParsedMeasurement(
            systolic=systolic,
            diastolic=diastolic,
            pulse=pulse,
            measured_at=measured_at,
            note=note,
        ),
        metrics=tuple(metrics),
        on_date=measured_at.date(),
        note=note,
    )


def parse_measurement(
    text: str, now: Optional[dt.datetime] = None
) -> Optional[ParsedMeasurement]:
    """Только давление. None — если его в тексте нет."""
    return parse_entry(text, now).measurement


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
