"""Общее форматирование: даты, склонения, деньги, текстовая графика."""

from __future__ import annotations

import datetime as dt
import html
from typing import Optional, Sequence

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

MONTHS_NOMINATIVE = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

SPARKS = "▁▂▃▄▅▆▇█"


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def plural(number: int, one: str, few: str, many: str) -> str:
    n = abs(number) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def duration(seconds: float) -> str:
    """Длительность по-человечески: 34 с, 5 мин 53 с, 1 ч 12 мин.

    Секунды перестают читаться где-то после минуты: «353 с» приходится
    делить в уме, а «5 мин 53 с» понятно сразу.
    """
    total = int(round(seconds))
    if total < 60:
        return f"{total} с"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} мин {secs:02d} с" if secs else f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes:02d} мин" if minutes else f"{hours} ч"


def days_word(count: int) -> str:
    return plural(count, "день", "дня", "дней")


def format_date(date: dt.date, today: Optional[dt.date] = None) -> str:
    today = today or dt.date.today()
    if date == today:
        return "сегодня"
    if date == today - dt.timedelta(days=1):
        return "вчера"
    if date == today - dt.timedelta(days=2):
        return "позавчера"
    if date.year == today.year:
        return f"{date.day} {MONTHS_GENITIVE[date.month - 1]}"
    return f"{date.day} {MONTHS_GENITIVE[date.month - 1]} {date.year}"


def format_moment(moment: dt.datetime, now: Optional[dt.datetime] = None) -> str:
    today = (now or dt.datetime.now()).date()
    return f"{format_date(moment.date(), today)} в {moment:%H:%M}"


def short_moment(moment: dt.datetime, now: Optional[dt.datetime] = None) -> str:
    """Компактно, для списков: «17.08 08:30» или «сегодня 08:30»."""
    today = (now or dt.datetime.now()).date()
    if moment.date() == today:
        return f"сегодня  {moment:%H:%M}"
    if moment.date() == today - dt.timedelta(days=1):
        return f"вчера    {moment:%H:%M}"
    return f"{moment:%d.%m}    {moment:%H:%M}"


def format_period(start: dt.date, end: dt.date) -> str:
    if start == end:
        return f"{start.day} {MONTHS_GENITIVE[start.month - 1]} {start.year}"
    if (start.month, start.year) == (end.month, end.year):
        return f"{start.day}–{end.day} {MONTHS_GENITIVE[start.month - 1]} {start.year}"
    if start.year == end.year:
        return (
            f"{start.day} {MONTHS_GENITIVE[start.month - 1]} — "
            f"{end.day} {MONTHS_GENITIVE[end.month - 1]} {start.year}"
        )
    return (
        f"{start.day} {MONTHS_GENITIVE[start.month - 1]} {start.year} — "
        f"{end.day} {MONTHS_GENITIVE[end.month - 1]} {end.year}"
    )


def bar(value: float, maximum: float, width: int = 10) -> str:
    if maximum <= 0 or value <= 0:
        return "░" * width
    filled = max(1, min(width, round(value / maximum * width)))
    return "█" * filled + "░" * (width - filled)


def sparkline(values: Sequence[float]) -> str:
    """Мини-график из блочных символов: ▁▂▅▇▃."""
    numbers = [float(value) for value in values]
    if not numbers:
        return ""
    low, high = min(numbers), max(numbers)
    if high - low < 1e-9:
        return SPARKS[len(SPARKS) // 2] * len(numbers)
    step = (high - low) / (len(SPARKS) - 1)
    return "".join(SPARKS[int(round((value - low) / step))] for value in numbers)



NBSP = "\u00a0"


def format_money(minor: int, currency: str = "₽") -> str:
    """125050 → «1 250,50 ₽», 30000 → «300 ₽»."""
    sign = "−" if minor < 0 else ""
    major, cents = divmod(abs(int(minor)), 100)
    grouped = f"{major:,}".replace(",", NBSP)
    tail = f",{cents:02d}" if cents else ""
    return f"{sign}{grouped}{tail}{NBSP}{currency}"
