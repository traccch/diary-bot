"""Материал для английского: карточки и квесты-сцены.

Набор собран не «по алфавиту», а под цель: понимать игры, фильмы и песни без
перевода. Поэтому здесь три слоя:

* `core` — частотные слова, из которых состоит почти любая живая речь;
* тематические паки (`games`, `films`, `music`) — то, что встречается в
  интерфейсах, диалогах и текстах песен;
* `phrasal` — фразовые глаголы: их не понять по отдельным словам, а без них
  не понять ни одного диалога.

У каждой карточки есть пример-предложение: слово запоминается в контексте,
а не списком. Пак легко расширять — достаточно дописать строки.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class Card:
    id: str
    en: str
    ru: str
    example: str
    example_ru: str
    pack: str
    level: int  # 1 — самое ходовое, 3 — уже посложнее


@dataclass(frozen=True)
class Pack:
    key: str
    title: str
    icon: str
    about: str


PACKS: tuple[Pack, ...] = (
    Pack("core", "Основа", "🧱", "частотные слова, без которых не обойтись"),
    Pack("games", "Игры", "🎮", "интерфейс, квесты, бой, инвентарь"),
    Pack("films", "Кино", "🎬", "живые реплики и то, что за ними стоит"),
    Pack("music", "Музыка", "🎵", "образы и идиомы из песен"),
    Pack("phrasal", "Фразовые", "🔗", "глаголы, которые меняют смысл предлогом"),
)

PACK_BY_KEY = {pack.key: pack for pack in PACKS}


def _cards(pack: str, level: int, rows: Sequence[tuple[str, str, str, str]]) -> list[Card]:
    made = []
    for en, ru, example, example_ru in rows:
        key = en.lower().replace(" ", "-").replace("'", "")
        made.append(
            Card(
                id=f"{pack}:{key}",
                en=en,
                ru=ru,
                example=example,
                example_ru=example_ru,
                pack=pack,
                level=level,
            )
        )
    return made


CARDS: list[Card] = []

# --------------------------------------------------------------------- основа

CARDS += _cards("core", 1, [
    ("though", "хотя, впрочем", "It's cold, though I like it.", "Холодно, хотя мне нравится."),
    ("enough", "достаточно", "We don't have enough time.", "У нас недостаточно времени."),
    ("actually", "на самом деле", "Actually, I was wrong.", "На самом деле я ошибался."),
    ("maybe", "может быть", "Maybe he forgot.", "Может быть, он забыл."),
    ("almost", "почти", "I almost fell asleep.", "Я почти уснул."),
    ("already", "уже", "She has already left.", "Она уже ушла."),
    ("still", "всё ещё", "He is still waiting.", "Он всё ещё ждёт."),
    ("instead", "вместо этого", "Let's walk instead.", "Давай лучше пройдёмся."),
    ("whatever", "что угодно; да как хочешь", "Whatever you say.", "Как скажешь."),
    ("somehow", "как-то, каким-то образом", "Somehow it works.", "Как-то оно работает."),
    ("nearly", "почти", "It's nearly done.", "Почти готово."),
    ("hardly", "едва ли", "I hardly know him.", "Я его почти не знаю."),
    ("mean", "иметь в виду", "What do you mean?", "Что ты имеешь в виду?"),
    ("guess", "догадываться, полагать", "I guess you're right.", "Наверное, ты прав."),
    ("keep", "продолжать; хранить", "Keep going.", "Продолжай."),
    ("let", "позволять, давать", "Let me try.", "Дай я попробую."),
    ("wait", "ждать", "Wait for me.", "Подожди меня."),
    ("need", "нуждаться", "You need a break.", "Тебе нужен перерыв."),
    ("care", "заботиться; быть небезразличным", "I don't care.", "Мне всё равно."),
    ("mind", "возражать; разум", "Never mind.", "Неважно, забудь."),
    ("sure", "уверен; конечно", "Are you sure?", "Ты уверен?"),
    ("since", "с тех пор как; поскольку", "Since then, nothing changed.", "С тех пор ничего не изменилось."),
    ("unless", "если не", "Don't call unless it's urgent.", "Не звони, если это не срочно."),
    ("whether", "ли", "I don't know whether to go.", "Не знаю, идти ли."),
])

CARDS += _cards("core", 2, [
    ("afford", "позволить себе", "I can't afford it.", "Я не могу себе это позволить."),
    ("borrow", "брать взаймы", "Can I borrow your pen?", "Можно одолжить ручку?"),
    ("owe", "быть должным", "You owe me one.", "С тебя должок."),
    ("worth", "стоящий, стоит того", "It's worth trying.", "Стоит попробовать."),
    ("spare", "лишний; щадить", "Do you have a spare minute?", "Есть свободная минутка?"),
    ("bother", "беспокоить; утруждаться", "Don't bother.", "Не утруждайся."),
    ("weird", "странный", "That's weird.", "Это странно."),
    ("tough", "трудный; жёсткий", "Tough luck.", "Не повезло."),
    ("plenty", "много, вдоволь", "There's plenty of time.", "Времени полно."),
    ("barely", "еле-еле", "I barely slept.", "Я почти не спал."),
    ("rather", "скорее; довольно", "I'd rather stay.", "Я лучше останусь."),
    ("indeed", "действительно", "Very good indeed.", "И правда, очень хорошо."),
    ("meanwhile", "тем временем", "Meanwhile, he waited.", "Тем временем он ждал."),
    ("otherwise", "иначе", "Hurry, otherwise we're late.", "Поспеши, иначе опоздаем."),
    ("besides", "кроме того", "Besides, it's cheap.", "К тому же это дёшево."),
    ("eventually", "в конце концов", "Eventually she agreed.", "В конце концов она согласилась."),
])

# ---------------------------------------------------------------------- игры

CARDS += _cards("games", 1, [
    ("quest", "задание, квест", "Open the quest log.", "Открой журнал заданий."),
    ("loot", "добыча, лут", "Grab the loot and run.", "Хватай добычу и беги."),
    ("gear", "снаряжение", "Upgrade your gear.", "Улучши снаряжение."),
    ("skill", "навык", "Unlock a new skill.", "Открой новый навык."),
    ("level up", "повысить уровень", "You leveled up!", "Ты повысил уровень!"),
    ("health", "здоровье", "Your health is low.", "У тебя мало здоровья."),
    ("damage", "урон", "This sword deals more damage.", "Этот меч наносит больше урона."),
    ("shield", "щит", "Raise your shield.", "Подними щит."),
    ("enemy", "враг", "Enemies ahead.", "Впереди враги."),
    ("weapon", "оружие", "Choose your weapon.", "Выбери оружие."),
    ("armor", "броня", "Heavy armor slows you down.", "Тяжёлая броня замедляет."),
    ("spell", "заклинание", "Cast a spell.", "Прочитай заклинание."),
    ("save", "сохранение; сохранить", "Save before the boss.", "Сохранись перед боссом."),
    ("checkpoint", "контрольная точка", "You reached a checkpoint.", "Ты достиг контрольной точки."),
    ("inventory", "инвентарь", "Your inventory is full.", "Инвентарь переполнен."),
    ("craft", "мастерить, крафтить", "Craft a healing potion.", "Скрафти зелье лечения."),
    ("reward", "награда", "Claim your reward.", "Забери награду."),
    ("dungeon", "подземелье", "Clear the dungeon.", "Зачисти подземелье."),
    ("stealth", "скрытность", "Try a stealth approach.", "Попробуй пройти скрытно."),
    ("ammo", "патроны", "I'm out of ammo.", "У меня кончились патроны."),
])

CARDS += _cards("games", 2, [
    ("bounty", "награда за голову", "There's a bounty on him.", "За его голову назначена награда."),
    ("summon", "призывать", "Summon a companion.", "Призови спутника."),
    ("wield", "владеть оружием", "You can wield two blades.", "Ты можешь держать два клинка."),
    ("forge", "ковать; кузница", "Forge a better blade.", "Выкуй клинок получше."),
    ("siege", "осада", "The siege lasted for days.", "Осада длилась несколько дней."),
    ("betray", "предать", "He betrayed the guild.", "Он предал гильдию."),
    ("vault", "хранилище", "The vault is sealed.", "Хранилище запечатано."),
    ("scavenge", "добывать из мусора", "Scavenge for parts.", "Пособирай детали."),
    ("perk", "перк, бонус", "Pick a perk.", "Выбери перк."),
    ("respawn", "возрождение", "You respawn at the camp.", "Ты возрождаешься в лагере."),
])

# ---------------------------------------------------------------------- кино

CARDS += _cards("films", 1, [
    ("hold on", "погоди; держись", "Hold on, I'll explain.", "Погоди, я объясню."),
    ("come on", "да ладно; давай", "Come on, we're late.", "Давай же, мы опаздываем."),
    ("no way", "ни за что; да ладно", "No way that's true.", "Быть не может, чтобы это было правдой."),
    ("get it", "понять", "I don't get it.", "Я не понимаю."),
    ("stay away", "держись подальше", "Stay away from him.", "Держись от него подальше."),
    ("shut up", "замолчи", "Shut up and listen.", "Замолчи и слушай."),
    ("take care", "береги себя", "Take care of yourself.", "Береги себя."),
    ("what's up", "как дела; что случилось", "Hey, what's up?", "Привет, как оно?"),
    ("hang out", "тусоваться", "We hang out on weekends.", "Мы зависаем по выходным."),
    ("make sense", "иметь смысл", "That makes sense.", "Это логично."),
    ("keep an eye on", "приглядывать за", "Keep an eye on the door.", "Приглядывай за дверью."),
    ("in charge", "главный, ответственный", "Who's in charge here?", "Кто здесь главный?"),
    ("out of here", "прочь отсюда", "Let's get out of here.", "Валим отсюда."),
    ("on purpose", "нарочно", "You did it on purpose.", "Ты сделал это нарочно."),
    ("by the way", "кстати", "By the way, he called.", "Кстати, он звонил."),
])

CARDS += _cards("films", 2, [
    ("figure out", "разобраться, понять", "We'll figure it out.", "Мы разберёмся."),
    ("show up", "появиться", "He never showed up.", "Он так и не пришёл."),
    ("back off", "отступить, отвали", "Back off, man.", "Отвали, приятель."),
    ("hang in there", "держись", "Hang in there, it's almost over.", "Держись, уже почти конец."),
    ("call it a day", "закончить на сегодня", "Let's call it a day.", "На сегодня хватит."),
    ("out of the blue", "как гром среди ясного неба", "She called out of the blue.", "Она позвонила совершенно неожиданно."),
    ("piece of cake", "проще простого", "The test was a piece of cake.", "Тест был проще простого."),
    ("cut it out", "прекрати", "Cut it out, both of you.", "Прекратите оба."),
    ("owe someone", "быть в долгу", "I owe you big time.", "Я тебе сильно должен."),
    ("hold your horses", "не спеши", "Hold your horses, we have time.", "Не гони, у нас есть время."),
])

# -------------------------------------------------------------------- музыка

CARDS += _cards("music", 1, [
    ("heartbeat", "биение сердца", "I hear your heartbeat.", "Я слышу твоё сердцебиение."),
    ("shadow", "тень", "Dancing with my shadow.", "Танцую со своей тенью."),
    ("burn", "гореть", "We burn like fire.", "Мы горим, как огонь."),
    ("chase", "гнаться", "Chasing the sun.", "В погоне за солнцем."),
    ("fade", "угасать", "The lights fade away.", "Огни угасают."),
    ("breathe", "дышать", "Just breathe.", "Просто дыши."),
    ("hold me", "обними меня", "Hold me closer.", "Обними меня крепче."),
    ("forever", "навсегда", "Forever and a day.", "Навсегда и ещё день."),
    ("lost", "потерянный", "I feel lost tonight.", "Сегодня я чувствую себя потерянным."),
    ("rise", "подниматься", "We rise again.", "Мы поднимаемся снова."),
])

CARDS += _cards("music", 2, [
    ("longing", "тоска, томление", "A song full of longing.", "Песня, полная тоски."),
    ("bittersweet", "горько-сладкий", "A bittersweet goodbye.", "Горько-сладкое прощание."),
    ("aching", "ноющий, щемящий", "An aching heart.", "Щемящее сердце."),
    ("wander", "бродить", "I wander through the night.", "Я брожу сквозь ночь."),
    ("thunder", "гром", "Voices like thunder.", "Голоса как гром."),
    ("whisper", "шёпот; шептать", "Whisper my name.", "Прошепчи моё имя."),
    ("carry on", "продолжать", "Carry on, my friend.", "Продолжай, мой друг."),
    ("let go", "отпустить", "You gotta let go.", "Тебе нужно отпустить."),
])

# ---------------------------------------------------------------- фразовые

CARDS += _cards("phrasal", 1, [
    ("give up", "сдаться", "Don't give up now.", "Не сдавайся сейчас."),
    ("find out", "выяснить", "I'll find out tomorrow.", "Я выясню завтра."),
    ("look for", "искать", "I'm looking for my keys.", "Я ищу ключи."),
    ("come back", "вернуться", "Come back soon.", "Возвращайся скорее."),
    ("turn on", "включить", "Turn on the light.", "Включи свет."),
    ("turn off", "выключить", "Turn off the music.", "Выключи музыку."),
    ("put on", "надеть", "Put on your coat.", "Надень пальто."),
    ("take off", "снять; взлететь", "Take off your shoes.", "Сними обувь."),
    ("pick up", "поднять; забрать", "Pick up the phone.", "Возьми трубку."),
    ("get up", "вставать", "I get up at seven.", "Я встаю в семь."),
    ("run out", "закончиться", "We ran out of milk.", "У нас кончилось молоко."),
    ("work out", "сработать; тренироваться", "It worked out fine.", "Всё вышло нормально."),
])

CARDS += _cards("phrasal", 2, [
    ("put up with", "мириться с", "I can't put up with this.", "Я не могу это терпеть."),
    ("look forward to", "с нетерпением ждать", "I look forward to it.", "Жду с нетерпением."),
    ("come across", "наткнуться на", "I came across an old photo.", "Я наткнулся на старое фото."),
    ("get along", "ладить", "They get along well.", "Они хорошо ладят."),
    ("bring up", "поднять тему; воспитывать", "Don't bring that up.", "Не поднимай эту тему."),
    ("turn out", "оказаться", "It turned out to be true.", "Это оказалось правдой."),
    ("hold back", "сдерживать", "Don't hold back.", "Не сдерживайся."),
    ("break down", "сломаться", "The car broke down.", "Машина сломалась."),
])

BY_ID = {card.id: card for card in CARDS}
BY_LEVEL: dict[int, list[Card]] = {}
for _card in CARDS:
    BY_LEVEL.setdefault(_card.level, []).append(_card)


def card_of(item_id: str) -> Optional[Card]:
    return BY_ID.get(item_id)


def all_ids() -> list[str]:
    return [card.id for card in CARDS]


def cards_of_pack(pack: str) -> list[Card]:
    return [card for card in CARDS if card.pack == pack]
