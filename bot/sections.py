"""Разделы бота: давление, деньги и английский.

Раздел — это не изоляция, а точка сборки: он решает, куда пойдёт свободный
текст, если сообщение можно понять двояко, и какие команды показывать.
Явно распознанное всегда важнее выбранного раздела: «120/80» попадёт
в дневник давления, даже если ты сейчас в разделе денег.
"""

from __future__ import annotations

from dataclasses import dataclass

PRESSURE = "pressure"
MONEY = "money"
ENGLISH = "english"

#: Тема напоминаний без своего раздела: мягкие вопросы про сон, шаги, пульс.
#: Отдельным пунктом меню это было бы ещё одной анкетой, которую надо помнить
#: и открывать; смысл как раз в том, чтобы показатели набирались по дороге.
HEALTH = "health"

#: Ещё одна тема без раздела: утренний пробег.
CAR = "car"


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    icon: str
    hint: str

    @property
    def label(self) -> str:
        return f"{self.icon} {self.title}"


SECTIONS: tuple[Section, ...] = (
    Section(
        key=PRESSURE,
        title="Давление",
        icon="🩺",
        hint="<code>120/80 68</code> · <code>сон 23:21-7:01</code> · <code>вес 78,5</code>",
    ),
    Section(
        key=MONEY,
        title="Деньги",
        icon="💰",
        hint="<code>кофе 300</code> · <code>такси 450 вчера</code> · <code>+90000 зарплата</code>",
    ),
    Section(
        key=ENGLISH,
        title="Английский",
        icon="🇬🇧",
        hint="карточки и квесты по пять минут · пришли слово — переведу",
    ),
)

HEALTH_TOPIC = Section(
    key=HEALTH,
    title="Самочувствие",
    icon="🫀",
    hint="сон, шаги, пульс покоя — одним нажатием",
)

CAR_TOPIC = Section(
    key=CAR,
    title="Пробег",
    icon="🚗",
    hint="число с одометра по утрам",
)

#: Всё, к чему может быть привязано напоминание: разделы и темы без раздела.
TOPICS: tuple[Section, ...] = (*SECTIONS, HEALTH_TOPIC, CAR_TOPIC)

BY_KEY = {section.key: section for section in SECTIONS}
TOPICS_BY_KEY = {section.key: section for section in TOPICS}
DEFAULT = PRESSURE


def section_of(key: str) -> Section:
    return BY_KEY.get(key, BY_KEY[DEFAULT])


def topic_of(key: str) -> Section:
    """То же для тем напоминаний: у «самочувствия» раздела нет, а имя нужно."""
    return TOPICS_BY_KEY.get(key, BY_KEY[DEFAULT])
