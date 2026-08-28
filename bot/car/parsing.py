"""Разбор строки с пробегом: «пробег 203116», «203116 км», «одометр 203116»."""

from __future__ import annotations

import re
from typing import Optional

from .db import MAX_KM

#: Слово рядом с числом обязательно: голое «203116» — это скорее сумма.
_WORD = r"(?:пробег|пробега|одометр|одометре|км|километ\w*)"
_PATTERNS = (
    re.compile(_WORD + r"\D{0,3}(\d[\d\s.,]{2,9})", re.IGNORECASE),
    re.compile(r"(\d[\d\s.,]{2,9})\s*" + _WORD, re.IGNORECASE),
)


def parse_mileage(text: str) -> Optional[int]:
    """Число с одометра или None, если в строке его нет."""
    for pattern in _PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        digits = re.sub(r"[^\d]", "", match.group(1))
        if not digits:
            continue
        km = int(digits)
        if 0 < km <= MAX_KM:
            return km
    return None


def looks_like_mileage(text: str) -> bool:
    return parse_mileage(text) is not None


def strip_mileage(text: str) -> str:
    """Убирает из строки кусок про пробег, оставляя остальное как было.

    «бензин 1999 пробег 203116» — это и трата, и показание одометра. Если
    не вырезать пробег, разбор трат увидит в нём сумму в двести тысяч.
    """
    for pattern in _PATTERNS:
        match = pattern.search(text or "")
        if match:
            return (text[: match.start()] + " " + text[match.end():]).strip()
    return text
