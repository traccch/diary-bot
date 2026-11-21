"""Интервальные повторения: когда показывать карточку снова.

Схема простая и проверенная временем — коробки Лейтнера. Ответил верно —
карточка уезжает в следующую коробку и вернётся позже; ошибся — падает в
первую и вернётся завтра. Смысл в том, чтобы каждый день было мало работы,
а забытое всплывало раньше, чем успеет забыться совсем.

Ответ «не знаю» честнее ошибки: он не ухудшает статистику, но и не
продвигает карточку дальше.
"""

from __future__ import annotations

import datetime as dt
import html
import random
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from . import content

#: Через сколько дней карточка вернётся, по коробкам.
INTERVALS: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 64)
#: Коробка, начиная с которой слово считаем выученным.
LEARNED_BOX = 4

#: Сколько новых слов давать в день — больше не полезно, забудется.
NEW_PER_DAY = 6
#: Длина обычной сессии: три-четыре минуты, чтобы не превращалось в урок.
SESSION_SIZE = 10

#: Виды вопросов в сессии.
RECOGNIZE = "recognize"  # английское слово → перевод
RECALL = "recall"  # перевод → английское слово
CLOZE = "cloze"  # пропуск в предложении
TYPE = "type"  # перевод → напечатать слово самому

#: С какой коробки слово можно спрашивать «напиши сам»: узнавать его
#: уже умеешь, теперь надо уметь сказать.
TYPE_FROM_BOX = 2


@dataclass(frozen=True)
class Progress:
    item_id: str
    box: int
    due_on: dt.date
    seen: int
    correct: int
    lapses: int

    @property
    def learned(self) -> bool:
        return self.box >= LEARNED_BOX


def next_box(box: int, correct: bool) -> int:
    """Верный ответ двигает на коробку вперёд, ошибка — в самое начало."""
    if not correct:
        return 0
    return min(box + 1, len(INTERVALS) - 1)


def due_after(box: int, today: dt.date) -> dt.date:
    return today + dt.timedelta(days=INTERVALS[min(box, len(INTERVALS) - 1)])


@dataclass(frozen=True)
class Question:
    item_id: str
    kind: str
    prompt: str
    options: tuple[str, ...]
    correct: int
    hint: str


def _distractors(card: content.Card, field: str, count: int, rng: random.Random) -> list[str]:
    """Похожие, но неверные варианты: сначала из того же пака, потом любые."""
    same_pack = [item for item in content.cards_of_pack(card.pack) if item.id != card.id]
    others = [item for item in content.CARDS if item.id != card.id and item.pack != card.pack]
    rng.shuffle(same_pack)
    rng.shuffle(others)

    picked: list[str] = []
    for item in same_pack + others:
        value = getattr(item, field)
        if value not in picked:
            picked.append(value)
        if len(picked) == count:
            break
    return picked


def make_question(
    card: content.Card, kind: str, rng: Optional[random.Random] = None
) -> Question:
    """Собирает вопрос выбранного вида с тремя ложными вариантами."""
    rng = rng or random.Random()

    if kind == CLOZE and not card.example:
        kind = RECALL  # своё слово без примера: пропуск подставлять некуда

    if kind == TYPE:
        prompt = f"✍️ Напиши по-английски: <b>{card.ru}</b>"
        if card.example:
            gap = "…" * 3
            hidden = re.sub(re.escape(card.en), gap, card.example, 1, re.IGNORECASE)
            if gap in hidden:
                prompt += f"\n\n<i>{hidden}</i>"
        return Question(
            item_id=card.id,
            kind=kind,
            prompt=prompt,
            options=(card.en,),
            correct=0,
            hint=f"{card.en} — {card.ru}",
        )

    if kind == RECALL:
        prompt = f"Как по-английски <b>{card.ru}</b>?"
        right, field = card.en, "en"
    elif kind == CLOZE:
        gap = "…" * 3
        # регистр не важен: «No way, you beat him?» прячет и «no way»
        sentence = re.sub(re.escape(card.en), f"<u>{gap}</u>", card.example, 1, re.IGNORECASE)
        if gap not in sentence:  # слово в примере в другой форме — покажем перевод
            sentence = card.example
            if card.example_ru:
                sentence += f"\n<i>{card.example_ru}</i>"
        prompt = f"Какое слово пропущено?\n\n{sentence}"
        right, field = card.en, "en"
    else:
        prompt = f"Что значит <b>{card.en}</b>?"
        right, field = card.ru, "ru"

    options = [right] + _distractors(card, field, 3, rng)
    rng.shuffle(options)
    return Question(
        item_id=card.id,
        kind=kind,
        prompt=prompt,
        options=tuple(options),
        correct=options.index(right),
        hint=f"{card.en} — {card.ru}\n<i>{card.example}</i>",
    )


def kind_for(progress: Optional[Progress], rng: Optional[random.Random] = None) -> str:
    """Новое слово сначала просто узнать, дальше — вспомнить и подставить."""
    rng = rng or random.Random()
    if progress is None or progress.seen == 0:
        return RECOGNIZE
    if progress.box < TYPE_FROM_BOX:
        return rng.choice([RECOGNIZE, RECALL])
    return rng.choice([RECALL, CLOZE, TYPE])


EXACT = "exact"
TYPO = "typo"

_NOT_LETTERS = re.compile(r"[^\w' -]+")


def _normalize(text: str) -> str:
    text = html.unescape(text).lower().replace("’", "'").replace("`", "'")
    text = _NOT_LETTERS.sub(" ", text)
    return " ".join(text.replace("-", " ").split())


def _distance(left: str, right: str) -> int:
    """Расстояние Левенштейна — сколько букв поправить, чтобы совпало."""
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def check_typed(answer: str, typed: str) -> Optional[str]:
    """Сверяет напечатанное: EXACT, TYPO (одна-две опечатки) или None.

    Регистр, апостроф ’ вместо ' и точка в конце — не ошибки. Опечатка
    засчитывается: проверяем, помнишь ли слово, а не печать на телефоне.
    """
    want, got = _normalize(answer), _normalize(typed)
    if not got:
        return None
    if want == got:
        return EXACT
    allowed = 0 if len(want) < 4 else 1 if len(want) < 8 else 2
    if allowed and _distance(want, got) <= allowed:
        return TYPO
    return None


def build_session(
    progress: Sequence[Progress],
    today: dt.date,
    size: int = SESSION_SIZE,
    new_per_day: int = NEW_PER_DAY,
    new_today: int = 0,
    rng: Optional[random.Random] = None,
) -> list[str]:
    """Что показать сейчас: сначала просроченное, потом немного новых слов."""
    rng = rng or random.Random()
    known = {item.item_id: item for item in progress}

    due = [item.item_id for item in progress if item.due_on <= today]
    # Сначала то, что забывалось чаще: такие слова и держат уровень.
    due.sort(key=lambda item_id: (-known[item_id].lapses, known[item_id].due_on))

    room_for_new = max(0, min(new_per_day - new_today, size - len(due)))
    fresh: list[str] = []
    if room_for_new:
        candidates = [card for card in content.CARDS if card.id not in known]
        candidates.sort(key=lambda card: (card.level, card.pack))
        # Берём из головы списка, но не строго по порядку — иначе один пак подряд
        head = candidates[: room_for_new * 3]
        rng.shuffle(head)
        fresh = [card.id for card in head[:room_for_new]]

    return (due + fresh)[:size]
