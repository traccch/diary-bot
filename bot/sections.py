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

#: Малыш: утренний прогноз и совет по одежде.
BABY = "baby"


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
        key=ENGLISH,
        title="Английский",
        icon="🇬🇧",
        hint="карточки и квесты по пять минут · пришли слово — переведу",
    ),
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

BABY_TOPIC = Section(
    key=BABY,
    title="Малыш",
    icon="👶",
    hint="сон, замеры, погода и одежда на прогулку",
)

#: Всё, к чему может быть привязано напоминание: разделы и темы без раздела.
TOPICS: tuple[Section, ...] = (*SECTIONS, HEALTH_TOPIC, CAR_TOPIC, BABY_TOPIC)

BY_KEY = {section.key: section for section in SECTIONS}
TOPICS_BY_KEY = {section.key: section for section in TOPICS}
DEFAULT = PRESSURE


def section_of(key: str) -> Section:
    return BY_KEY.get(key, BY_KEY[DEFAULT])


def topic_of(key: str) -> Section:
    """То же для тем напоминаний: у «самочувствия» раздела нет, а имя нужно."""
    return TOPICS_BY_KEY.get(key, BY_KEY[DEFAULT])


def hidden_key(user_id: int) -> str:
    """Где хранится, какие разделы убраны с главного экрана (через запятую)."""
    return f"hidden_screens:{user_id}"


#: Разовая уборка для дневников, начатых до неё: давление, деньги и
#: самочувствие уходят с глаз, английский выходит вперёд. Ключи — экраны
#: главного меню, значения — темы их напоминаний.
TIDY_SCREENS = {"bp": PRESSURE, "money": MONEY, "health": HEALTH}
TIDY_FLAG = "tidy_v1"

#: Темы, о которых бот молчит по просьбе хозяина: давление, траты, шаги и сон
#: взрослого, пробег. Английский и малыш остаются.
QUIET_TOPICS = (PRESSURE, MONEY, HEALTH, CAR)
QUIET_FLAG = "quiet_v2"
