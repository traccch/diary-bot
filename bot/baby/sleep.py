"""Сон малыша: разбор записей и подсчёты. Без базы и Telegram — только логика.

Записывать удобно тремя способами:

* кнопками «😴 Уснул» / «🌞 Проснулся» — время берётся текущее;
* текстом: «уснул», «уснул 13:20», «проснулся в 7:05»;
* сразу промежутком: «сон 13:20-14:45» — если забыли нажать вовремя.

Ночной сон отличается от дневного не по часам на циферблате, а по смыслу:
начался вечером или ночью — или длился дольше пяти часов.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Optional, Sequence

START = "start"
END = "end"
RANGE = "range"

#: Позже этого часа или раньше NIGHT_UNTIL уснул — значит, на ночь.
NIGHT_FROM = 19
NIGHT_UNTIL = 5
#: Сон длиннее этого — ночной, во сколько бы ни начался.
NIGHT_MIN_HOURS = 5
#: Запись «в будущем» больше чем на столько — это про вчера.
FUTURE_SLACK = dt.timedelta(minutes=10)

_TIME = r"(\d{1,2})[:.](\d{2})"
_START_WORDS = r"(?:уснул[аи]?|заснул[аи]?|усн[уё]л|лег(?:ла)?\s+спать|л[её]г\s+спать|спит|уложил[аи]?)"
_END_WORDS = r"(?:проснул(?:ся|ась|ись)|встал[аи]?|подъ[её]м)"
_RANGE_WORDS = r"(?:сон|спал[аи]?|поспал[аи]?)"

_START = re.compile(rf"^\s*{_START_WORDS}(?:\s+(?:в\s+)?{_TIME})?\s*[.!]?\s*$", re.IGNORECASE)
_END = re.compile(rf"^\s*{_END_WORDS}(?:\s+(?:в\s+)?{_TIME})?\s*[.!]?\s*$", re.IGNORECASE)
_RANGE = re.compile(
    rf"^\s*{_RANGE_WORDS}\s+(?:с\s+)?{_TIME}\s*(?:[-–—]|до|по)\s*{_TIME}\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class Command:
    kind: str
    start: Optional[dt.datetime] = None
    end: Optional[dt.datetime] = None


@dataclass(frozen=True)
class Sleep:
    id: int
    start: dt.datetime
    end: Optional[dt.datetime]

    def length(self, now: dt.datetime) -> dt.timedelta:
        return (self.end or now) - self.start

    def is_night(self, now: dt.datetime) -> bool:
        if self.start.hour >= NIGHT_FROM or self.start.hour < NIGHT_UNTIL:
            return True
        return self.length(now) >= dt.timedelta(hours=NIGHT_MIN_HOURS)


def _at(now: dt.datetime, hour: str, minute: str) -> Optional[dt.datetime]:
    h, m = int(hour), int(minute)
    if h > 23 or m > 59:
        return None
    moment = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if moment > now + FUTURE_SLACK:  # «уснул 23:10», написанное в половине первого
        moment -= dt.timedelta(days=1)
    return moment


def parse(text: str, now: dt.datetime) -> Optional[Command]:
    """Команда про сон или None, если текст не про это."""
    match = _START.match(text)
    if match:
        at = _at(now, match.group(1), match.group(2)) if match.group(1) else now
        return Command(START, start=at) if at else None

    match = _END.match(text)
    if match:
        at = _at(now, match.group(1), match.group(2)) if match.group(1) else now
        return Command(END, end=at) if at else None

    match = _RANGE.match(text)
    if match:
        end = _at(now, match.group(3), match.group(4))
        if end is None:
            return None
        if int(match.group(1)) > 23 or int(match.group(2)) > 59:
            return None
        start = end.replace(hour=int(match.group(1)), minute=int(match.group(2)))
        if start >= end:  # «сон 21:30-6:40» — уснул накануне
            start -= dt.timedelta(days=1)
        return Command(RANGE, start=start, end=end)
    return None


def overlap(sleeps: Sequence[Sleep], since: dt.datetime, until: dt.datetime) -> dt.timedelta:
    """Сколько сна пришлось на промежуток."""
    total = dt.timedelta()
    for sleep in sleeps:
        left = max(sleep.start, since)
        right = min(sleep.end or until, until)
        if right > left:
            total += right - left
    return total


@dataclass(frozen=True)
class DaySummary:
    naps: tuple[Sleep, ...]
    nap_time: dt.timedelta
    night: Optional[Sleep]
    last_24h: dt.timedelta


def summarize(sleeps: Sequence[Sleep], now: dt.datetime) -> DaySummary:
    """Сегодняшние дневные сны, последняя ночь и сон за последние сутки."""
    today = now.date()
    naps = tuple(
        sleep for sleep in sleeps if sleep.start.date() == today and not sleep.is_night(now)
    )
    nights = [sleep for sleep in sleeps if sleep.is_night(now) and sleep.start <= now]
    night = max(nights, key=lambda sleep: sleep.start) if nights else None
    if night is not None and (now - night.start) > dt.timedelta(hours=30):
        night = None  # прошлая ночь не записана — старую не показываем
    return DaySummary(
        naps=naps,
        nap_time=sum((nap.length(now) for nap in naps), dt.timedelta()),
        night=night,
        last_24h=overlap(sleeps, now - dt.timedelta(hours=24), now),
    )


def clock(moment: dt.datetime) -> str:
    return moment.strftime("%H:%M")
