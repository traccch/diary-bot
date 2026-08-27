"""Мягкие вопросы про самочувствие: сон, шаги, пульс покоя, вес.

Это не задание и не квест, а обычный вопрос с готовыми ответами: нажал —
записалось. Смысл в том, чтобы показатели набирались сами собой, между делом,
а не когда специально сядешь заполнять дневник.

Правила, из которых всё вытекает:

* утром спрашиваем про сон, вечером — про то, что видно за день;
* про что уже записано сегодня, не спрашиваем вообще;
* вопрос дня один. Три вопроса подряд — это анкета, а анкеты закрывают.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Collection, Optional, Sequence

from .pressure import metrics

#: До этого часа спрашиваем про ночь, после — про прошедший день.
MORNING_UNTIL = dt.time(12, 0)


@dataclass(frozen=True)
class Choice:
    label: str
    #: Значение в единицах хранения: сон — минуты, вес — килограммы.
    value: float


@dataclass(frozen=True)
class Prompt:
    kind: str
    question: str
    hint: str
    choices: tuple[Choice, ...]
    #: Чем ответить, если готовых вариантов не хватает.
    manual: str

    @property
    def icon(self) -> str:
        kind = metrics.kind_of(self.kind)
        return kind.icon if kind else "•"


def _hours(*values: float) -> tuple[Choice, ...]:
    made = []
    for value in values:
        hours, minutes = divmod(int(round(value * 60)), 60)
        label = f"{hours} ч" if not minutes else f"{hours}:{minutes:02d}"
        made.append(Choice(label, value * 60))
    return tuple(made)


SLEEP = Prompt(
    kind=metrics.SLEEP.key,
    question="😴 <b>Как спалось?</b>\nСколько вышло за ночь?",
    hint="Точное время сна — <code>сон 23:20-7:05</code>.",
    choices=_hours(5, 6, 6.5, 7, 7.5, 8, 9),
    manual="сон 7ч30м",
)

STEPS = Prompt(
    kind=metrics.STEPS.key,
    question="👟 <b>Сколько сегодня прошёл?</b>\nПримерно, по счётчику в телефоне.",
    hint="Точное число — <code>шаги 8420</code>.",
    choices=(
        Choice("до 2 000", 1500),
        Choice("4 000", 4000),
        Choice("6 000", 6000),
        Choice("8 000", 8000),
        Choice("10 000", 10000),
        Choice("12 000+", 12000),
    ),
    manual="шаги 8420",
)

RESTING_PULSE = Prompt(
    kind=metrics.RESTING_PULSE.key,
    question="💓 <b>Пульс покоя за день?</b>\nТот, что показывает браслет или часы.",
    hint="Можно просто числом — <code>пульс покоя 58</code>.",
    choices=(
        Choice("50", 50),
        Choice("55", 55),
        Choice("60", 60),
        Choice("65", 65),
        Choice("70", 70),
        Choice("75", 75),
        Choice("80", 80),
    ),
    manual="пульс покоя 58",
)

WEIGHT = Prompt(
    kind=metrics.WEIGHT.key,
    question="⚖️ <b>Давно не взвешивался</b>\nЕсли встанешь на весы — пришли цифру.",
    hint="Например: <code>вес 78,5</code>.",
    # У веса шаг в сто граммов: кнопками такое не выбрать, только промахнуться.
    choices=(),
    manual="вес 78,5",
)

BY_KIND = {prompt.kind: prompt for prompt in (SLEEP, STEPS, RESTING_PULSE, WEIGHT)}

#: Вечерний порядок: чаще всего спрашиваем про шаги, реже — про пульс, изредка
#: про вес. Индекс — день недели, понедельник нулевой.
EVENING_BY_WEEKDAY: tuple[Prompt, ...] = (
    STEPS,
    RESTING_PULSE,
    STEPS,
    RESTING_PULSE,
    STEPS,
    WEIGHT,
    STEPS,
)


def _evening_order(day: dt.date) -> Sequence[Prompt]:
    first = EVENING_BY_WEEKDAY[day.weekday()]
    rest = [item for item in (STEPS, RESTING_PULSE, WEIGHT) if item is not first]
    return [first, *rest]


def pick(
    at: dt.time, day: dt.date, already: Collection[str] = ()
) -> Optional[Prompt]:
    """Вопрос для этого напоминания. None — спрашивать сегодня нечего."""
    order = [SLEEP] if at < MORNING_UNTIL else _evening_order(day)
    for prompt in order:
        if prompt.kind not in already:
            return prompt
    return None


def confirm(kind: str, value: float) -> str:
    """Короткое подтверждение записи — без назиданий и советов."""
    metric = metrics.kind_of(kind)
    if metric is None:
        return "Записал."
    return f"{metric.icon} Записал: <b>{metrics.format_value(kind, value)}</b>"


def render(prompt: Prompt) -> str:
    """Текст вопроса целиком: сам вопрос и подсказка, как ответить точнее."""
    return f"{prompt.question}\n\n<i>{prompt.hint}</i>"


def clean(kind: str, raw: str) -> Optional[float]:
    """Значение из нажатой кнопки. None — показатель или число не наши."""
    metric = metrics.kind_of(kind)
    if metric is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if metric.low <= value <= metric.high else None
