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

#: Сколько времени голое число считается ответом на вопрос.
LIVE_HOURS = 3

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
