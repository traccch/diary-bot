"""Вопрос, на который бот ждёт ответа.

Бот спросил про шаги — человек присылает «5182». Ни давление, ни трата на
это не похожи, и раньше он в ответ разводил руками: мол, не понял. Но
спрашивал-то он сам минуту назад, и помнить свой же вопрос — меньшее, что
можно ожидать.

Хранится в пометках базы: пережить перезапуск важнее, чем сэкономить строчку.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from .pressure import metrics, parsing

#: Сколько времени голое число считается ответом на вопрос.
LIVE_HOURS = 3

#: Голое число в ответ на вопрос о сне — это часы, а не минуты: «7,5» значит
#: семь с половиной часов. Больше шестнадцати часами не бывает, значит минуты.
BARE_HOURS_LIMIT = 16

#: Голое число: «5182», «7,5», «203 116». Всё остальное разбирается как обычно.
_NUMBER = re.compile(r"^\s*(\d[\d  ]*(?:[.,]\d+)?)\s*$")


def key(user_id: int) -> str:
    return f"asked:{user_id}"


def as_number(text: str) -> Optional[float]:
    """Число, если в строке нет ничего кроме него."""
    match = _NUMBER.match(text or "")
    if match is None:
        return None
    digits = match.group(1).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def as_answer(kind: str, text: str) -> Optional[float]:
    """Ответ на заданный вопрос — в тех единицах, в которых он хранится.

    Про сон спрашивают «сколько вышло за ночь», и отвечают на это как
    отвечают людям: «23:06-6:30», «7:20», «7,5». Слово «сон» человек не
    пишет — его только что написал сам бот, и подставить его — наша работа.
    """
    if kind == metrics.SLEEP.key:
        return _sleep(text)
    return as_number(text)


def _sleep(text: str) -> Optional[float]:
    """Минуты сна из ответа на вопрос. None — ответ не про сон."""
    raw = (text or "").strip()
    if not raw or not raw[0].isdigit():
        return None

    hours = as_number(raw)
    if hours is not None:
        return hours * 60 if hours <= BARE_HOURS_LIMIT else hours

    # «23:06-6:30» и «7:20» бот уже умеет читать — но со словом «сон».
    try:
        entry = parsing.parse_entry(f"сон {raw}")
    except parsing.ParseError:
        return None
    for metric in entry.metrics:
        if metric.kind == metrics.SLEEP.key:
            return metric.value
    return None


def remember(kind: str, now: dt.datetime) -> str:
    return f"{kind}|{now.strftime('%Y-%m-%d %H:%M')}"


def recall(raw: Optional[str], now: dt.datetime, hours: int = LIVE_HOURS) -> str:
    """О чём был вопрос. Пусто — вопроса не было или он давно протух."""
    if not raw or "|" not in raw:
        return ""
    kind, _, stamp = raw.partition("|")
    try:
        asked = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    except ValueError:
        return ""
    if not 0 <= (now - asked).total_seconds() <= hours * 3600:
        return ""
    return kind
