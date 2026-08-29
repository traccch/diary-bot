"""Числа словами — в цифры: «сто двадцать на восемьдесят» → «120 на 80».

Whisper пишет как слышит, и на русском числа у него выходят то цифрами, то
словами — предсказать нельзя. Разбор дневника ждёт цифры, поэтому слова
переводим здесь, до него.

Всё остальное в строке остаётся как было: «на», «пульс», «шаги» разбираются
дальше обычными правилами.
"""

from __future__ import annotations

import re
from typing import Optional

UNITS: dict[str, int] = {
    "ноль": 0, "нуль": 0,
    "один": 1, "одна": 1, "одно": 1, "раз": 1,
    "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19,
}

TENS: dict[str, int] = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}

HUNDREDS: dict[str, int] = {
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
}

#: Тысячи во всех падежах, которые выговаривает человек.
THOUSANDS = {"тысяча", "тысячи", "тысяч", "тысячу"}

_WORD = re.compile(r"[^\W\d_]+|\d+|[^\w\s]|\s+", re.UNICODE)


def _value(word: str) -> Optional[int]:
    clean = word.lower().replace("ё", "е")
    for table in (UNITS, TENS, HUNDREDS):
        if clean in table:
            return table[clean]
    return None


def words_to_numbers(text: str) -> str:
    """Заменяет числительные цифрами, не трогая остальное."""
    parts = _WORD.findall(text or "")
    out: list[str] = []

    current = 0        # накопленное внутри одной группы: «сто двадцать» → 120
    total = 0          # накопленное с тысячами: «восемь тысяч двести» → 8200
    started = False    # видели ли мы хоть одно числительное подряд
    gap = ""           # пробел, про который ещё неясно: он внутри числа или после

    def flush() -> None:
        """Дописывает накопленное число и возвращает отложенный пробел."""
        nonlocal current, total, started, gap
        if started:
            out.append(str(total + current))
            out.append(gap)
        current, total, started, gap = 0, 0, False, ""

    for part in parts:
        if part.isspace():
            if started:
                gap = part  # пробел внутри числа не разрывает его, но и не теряется
            else:
                out.append(part)
            continue

        lowered = part.lower().replace("ё", "е")
        if lowered in THOUSANDS:
            # «тысяча» без числа перед ней — это всё равно тысяча
            total += (current or 1) * 1000
            current, started, gap = 0, True, ""
            continue

        value = _value(part)
        if value is None:
            flush()
            out.append(part)
            continue

        # «сто двадцать» складываем, а «двадцать двадцать» — уже два числа
        if started and not _joins(current, value):
            flush()
        current += value
        started, gap = True, ""

    flush()
    return re.sub(r"[  ]{2,}", " ", "".join(out)).strip()


def _joins(current: int, value: int) -> bool:
    """Можно ли приклеить новое числительное к уже накопленному.

    Складываются только убывающие разряды: 100 + 20 + 8. «Двадцать сто» —
    это два разных числа, а не сто двадцать.
    """
    if current == 0:
        return True
    if value >= 100:
        return False
    if value >= 20:
        return current >= 100
    return current % 100 in (0, *range(20, 100, 10)) and current % 10 == 0
