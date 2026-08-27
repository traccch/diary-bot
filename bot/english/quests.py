"""Квесты: короткие сцены на английском с разбором.

Карточки дают слова, квест — ощущение живого языка: несколько реплик, как
в игре или фильме, и вопросы по смыслу. Читать нужно целиком, не переводя
пословно, — именно этот навык нужен, чтобы понимать на слух.

Каждый квест — один заход на пять минут: подсказка со словами, сцена,
три вопроса. Проходится один раз, но перечитать можно всегда.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class Question:
    text: str
    options: tuple[str, ...]
    correct: int
    explain: str = ""


@dataclass(frozen=True)
class Quest:
    id: str
    title: str
    icon: str
    setting: str
    vocab: tuple[tuple[str, str], ...]
    scene: tuple[str, ...]
    questions: tuple[Question, ...]

    @property
    def label(self) -> str:
        return f"{self.icon} {self.title}"

    @property
    def scene_text(self) -> str:
        return "\n".join(self.scene)


QUESTS: tuple[Quest, ...] = (
    Quest(
        id="tavern",
        title="Таверна",
        icon="🍺",
        setting="Ты заходишь в таверну. Классическая первая сцена любой RPG.",
        vocab=(
            ("innkeeper", "хозяин таверны"),
            ("rumor", "слух"),
            ("blade", "клинок"),
            ("head north", "идти на север"),
        ),
        scene=(
            "<b>Innkeeper:</b> You look like you've been on the road for days.",
            "<b>You:</b> Long enough. Any rumors worth hearing?",
            "<b>Innkeeper:</b> Folks say the mine went quiet last week. "
            "Nobody came back out.",
            "<b>You:</b> And nobody went to check?",
            "<b>Innkeeper:</b> The guard won't touch it. But if you've got a blade "
            "and no sense, head north at dawn.",
        ),
        questions=(
            Question(
                "Что случилось с шахтой?",
                ("Её закрыли на ремонт", "Оттуда никто не вернулся", "Её затопило"),
                1,
                "«The mine went quiet… nobody came back out» — стало тихо, никто не вышел.",
            ),
            Question(
                "Почему стража туда не идёт?",
                ("Им не платят", "Они не хотят с этим связываться", "Они уже там"),
                1,
                "«The guard won't touch it» — дословно «не притронется», то есть не берётся.",
            ),
            Question(
                "Что значит «if you've got a blade and no sense»?",
                (
                    "если у тебя есть меч и здравый смысл",
                    "если у тебя есть меч и нет мозгов",
                    "если ты умеешь драться",
                ),
                1,
                "«no sense» — «без соображения»; хозяин мягко называет затею глупой.",
            ),
        ),
    ),
    Quest(
        id="heist",
        title="Ограбление",
        icon="🎬",
        setting="Сцена из фильма: команда готовит дело. Много разговорных оборотов.",
        vocab=(
            ("pull it off", "провернуть, справиться"),
            ("back out", "пойти на попятную"),
            ("heads-up", "предупреждение"),
            ("in and out", "туда и обратно"),
        ),
        scene=(
            "<b>Mara:</b> Ten minutes. In and out. No noise.",
            "<b>Dev:</b> And if the alarm trips?",
            "<b>Mara:</b> Then you run. I'm not dragging anyone out of there twice.",
            "<b>Dev:</b> Look, if you want to back out, now's the time.",
            "<b>Mara:</b> I'm not backing out. I just want a heads-up before you "
            "improvise again.",
        ),
        questions=(
            Question(
                "Сколько времени у них на дело?",
                ("Десять минут", "Десять часов", "Столько, сколько нужно"),
                0,
            ),
            Question(
                "Что Мара говорит про тревогу?",
                ("Она её отключит", "Тогда надо бежать", "Ничего страшного"),
                1,
                "«Then you run» — коротко и по делу.",
            ),
            Question(
                "О чём просит Мара в конце?",
                (
                    "Чтобы её предупреждали заранее",
                    "Чтобы Дев вышел из дела",
                    "Чтобы никто не разговаривал",
                ),
                0,
                "«a heads-up» — предупреждение заранее.",
            ),
        ),
    ),
    Quest(
        id="song",
        title="Куплет песни",
        icon="🎵",
        setting="Текст песни — образы, а не буквальный смысл. Пробуй ловить настроение.",
        vocab=(
            ("hold on", "держаться"),
            ("tide", "прилив"),
            ("worn out", "измотанный"),
            ("make it through", "выдержать, дойти до конца"),
        ),
        scene=(
            "<i>I'm worn out from the waiting,</i>",
            "<i>the tide keeps pulling me back.</i>",
            "<i>But hold on — we're not done yet,</i>",
            "<i>we're gonna make it through the night.</i>",
        ),
        questions=(
            Question(
                "В каком состоянии герой в первой строке?",
                ("Полон сил", "Измотан ожиданием", "Счастлив"),
                1,
            ),
            Question(
                "Что значит «the tide keeps pulling me back»?",
                (
                    "его буквально уносит в море",
                    "обстоятельства тянут его назад",
                    "он любит море",
                ),
                1,
                "Прилив здесь — образ: что-то раз за разом отбрасывает назад.",
            ),
            Question(
                "Чем заканчивается куплет?",
                ("Надеждой продержаться", "Прощанием", "Сожалением"),
                0,
                "«we're gonna make it through the night» — мы переживём эту ночь.",
            ),
        ),
    ),
    Quest(
        id="shop",
        title="Торговец",
        icon="🎮",
        setting="Разговор с торговцем: цифры, торг и вежливый отказ.",
        vocab=(
            ("worth", "стоит"),
            ("rip-off", "грабёж, обдираловка"),
            ("throw in", "добавить сверху"),
            ("deal", "сделка; по рукам"),
        ),
        scene=(
            "<b>Trader:</b> Fifty gold for the blade. It's worth twice that.",
            "<b>You:</b> Fifty? That's a rip-off.",
            "<b>Trader:</b> Then walk. But you won't find better in this valley.",
            "<b>You:</b> Forty, and you throw in the whetstone.",
            "<b>Trader:</b> ...Deal. But don't tell anyone what you paid.",
        ),
        questions=(
            Question(
                "Сколько в итоге заплатил игрок?",
                ("Пятьдесят", "Сорок", "Ничего"),
                1,
            ),
            Question(
                "Что значит «Then walk»?",
                ("«Тогда иди пешком»", "«Тогда проходи мимо»", "«Тогда беги»"),
                1,
                "Разговорное «walk» — уходи, не покупай.",
            ),
            Question(
                "Что игрок попросил добавить?",
                ("Точильный камень", "Ещё один клинок", "Скидку на броню"),
                0,
                "«throw in the whetstone» — добавить точильный камень к сделке.",
            ),
        ),
    ),
    Quest(
        id="radio",
        title="Радиосвязь",
        icon="📻",
        setting="Короткие рубленые фразы — как в шутерах и фильмах про военных.",
        vocab=(
            ("copy that", "принял"),
            ("stand by", "оставайся на связи, жди"),
            ("hostile", "противник"),
            ("fall back", "отходить"),
        ),
        scene=(
            "<b>Command:</b> Bravo, what's your status?",
            "<b>Bravo:</b> Two hostiles down. One's still moving.",
            "<b>Command:</b> Copy that. Stand by for support.",
            "<b>Bravo:</b> Negative, we're low on ammo. Requesting permission "
            "to fall back.",
            "<b>Command:</b> Permission granted. Get out of there.",
        ),
        questions=(
            Question(
                "Что означает «Copy that»?",
                ("«Скопируй это»", "«Принял»", "«Повтори»"),
                1,
            ),
            Question(
                "Почему Браво просит разрешения отойти?",
                ("Кончаются патроны", "Устали", "Потеряли связь"),
                0,
                "«we're low on ammo» — мало боеприпасов.",
            ),
            Question(
                "Чем ответило командование?",
                ("Отказало", "Разрешило отход", "Приказало ждать"),
                1,
                "«Permission granted» — разрешение получено.",
            ),
        ),
    ),
)

BY_ID = {quest.id: quest for quest in QUESTS}


def quest_of(quest_id: str) -> Optional[Quest]:
    return BY_ID.get(quest_id)


def next_quest(done: Sequence[str]) -> Optional[Quest]:
    """Первый непройденный квест — порядок задуман от простого к живому."""
    for quest in QUESTS:
        if quest.id not in done:
            return quest
    return None
