"""Загрузка конфигурации из переменных окружения / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import find_dotenv, load_dotenv

from .voice import VoiceConfig


@dataclass(frozen=True)
class Config:
    token: str
    db_path: str
    default_tz: str
    log_level: str
    #: Кому разрешено обновлять бота. Не задан — хозяином считается тот,
    #: кто первым написал боту.
    owner_id: Optional[int]
    #: Кому вообще можно писать боту. Пусто — только хозяину (тому, кто
    #: написал первым, или тому, кто указан в OWNER_ID).
    allowed_users: frozenset[int]
    #: Что делать с новой версией: «install» — ставить сам, «notify» — сказать
    #: и ждать кнопку, «off» — не смотреть вовсе.
    auto_update: str
    #: Как часто смотреть, не вышло ли обновление (в минутах).
    auto_update_minutes: int
    #: Сколько секунд держать запрос обновлений открытым. Через фильтрующие
    #: сети длинный запрос обрывают на полуслове, поэтому по умолчанию короче
    #: аиограмовских 30 секунд.
    polling_timeout: int
    #: Через какой прокси ходить к Telegram. Пусто — напрямую.
    proxy: str
    #: Куда писать журнал файлом.
    log_path: str
    #: Из какого файла прочитаны настройки. Пусто — файла не нашлось.
    env_file: str
    #: Похожие файлы рядом: .env.txt от Блокнота и прочее «почти .env».
    env_lookalikes: tuple[str, ...]
    voice: VoiceConfig


#: Схемы, которые понимает клиент. Всё остальное — не прокси.
PROXY_SCHEMES = ("http://", "https://", "socks5://", "socks5h://", "socks4://")

#: Ключи VPN-приложений. Это не прокси: за ними стоит свой протокол,
#: который умеет разбирать только клиент вроде Happ, v2rayN или sing-box.
VPN_KEY_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://", "ssconf://", "hysteria")

PROXY_HELP = (
    "TELEGRAM_PROXY={value!r} — это не адрес прокси.\n"
    "  Нужен адрес вида socks5://127.0.0.1:2080 или http://127.0.0.1:8080.\n"
    "  Ключ VPN (vless://…, vmess://…) сюда не подходит: его понимает только\n"
    "  само VPN-приложение. Включи в нём режим локального прокси и впиши сюда\n"
    "  адрес и порт, которые оно показывает."
)


#: Просьба найти прокси самому.
AUTO = "auto"


def read_proxy(raw: str) -> str:
    """Проверяет адрес прокси. Непонятное — лучше отвергнуть громко."""
    value = raw.strip().strip('"').strip("'")
    if not value:
        return ""
    if value.lower() in {AUTO, "авто", "сам"}:
        return AUTO
    lowered = value.lower()
    if lowered.startswith(PROXY_SCHEMES):
        return value
    if lowered.startswith(VPN_KEY_SCHEMES):
        raise RuntimeError(PROXY_HELP.format(value=value[:24] + "…"))
    if ":" in value and "//" not in value:
        # «127.0.0.1:2080» — понятно, что имелось в виду
        return "socks5://" + value
    raise RuntimeError(PROXY_HELP.format(value=value[:40]))


#: Режимы обновления.
INSTALL = "install"
NOTIFY = "notify"
OFF = "off"

#: Как часто смотреть за обновлениями. Чаще минуты — это уже не про обновления.
DEFAULT_UPDATE_MINUTES = 5
MIN_UPDATE_MINUTES, MAX_UPDATE_MINUTES = 1, 24 * 60


def read_auto_update(raw: str, legacy: str = "") -> str:
    """Режим обновления. Старое AUTO_UPDATE_CHECK=0 по-прежнему выключает всё."""
    value = raw.strip().lower()
    if value in {INSTALL, NOTIFY, OFF}:
        return value
    if value in {"1", "true", "yes", "on", "auto", "сам"}:
        return INSTALL
    if value in {"0", "false", "no"}:
        return OFF
    if legacy.strip().lower() in {"0", "false", "no", "off"}:
        return OFF
    return INSTALL


def read_minutes(raw: str) -> int:
    value = raw.strip()
    minutes = int(value) if value.isdigit() else DEFAULT_UPDATE_MINUTES
    return max(MIN_UPDATE_MINUTES, min(minutes, MAX_UPDATE_MINUTES))


def read_allowed(raw: str, owner_id: Optional[int]) -> frozenset[int]:
    """Список тех, кому можно писать боту. Хозяин в нём всегда."""
    found = {
        int(part)
        for part in raw.replace(",", " ").split()
        if part.lstrip("-").isdigit()
    }
    if owner_id is not None:
        found.add(owner_id)
    return frozenset(found)


#: Как Блокнот и проводник калечат имя файла настроек.
LOOKALIKES = (".env.txt", ".env.env", "env", "env.txt", ".env.ini", ".env.cfg")


def find_lookalikes(env_file: str) -> tuple[str, ...]:
    """Файлы, которые человек мог принять за .env.

    Блокнот в Windows дописывает .txt, а проводник расширение прячет — и
    получается, что правки уходят в файл, который никто не читает. Ошибка
    выглядит как «настройка не работает», и догадаться про неё невозможно.
    """
    folder = os.path.dirname(os.path.abspath(env_file or ".env")) or "."
    found = []
    for name in LOOKALIKES:
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate):
            found.append(candidate)
    return tuple(found)


def load_config() -> Config:
    env_file = find_dotenv(usecwd=True)
    load_dotenv(env_file)

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Скопируй .env.example в .env и впиши токен от @BotFather."
        )

    tz = os.getenv("DEFAULT_TZ", "Europe/Moscow").strip() or "Europe/Moscow"
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RuntimeError(f"DEFAULT_TZ={tz!r} — неизвестный часовой пояс") from exc

    raw_polling = os.getenv("POLLING_TIMEOUT", "15").strip()
    polling_timeout = int(raw_polling) if raw_polling.isdigit() else 15
    polling_timeout = max(1, min(polling_timeout, 50))

    raw_owner = os.getenv("OWNER_ID", "").strip()
    owner_id = int(raw_owner) if raw_owner.lstrip("-").isdigit() else None

    return Config(
        token=token,
        db_path=os.getenv("DB_PATH", "data/diary.db").strip() or "data/diary.db",
        default_tz=tz,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        owner_id=owner_id,
        allowed_users=read_allowed(os.getenv("ALLOWED_USERS", ""), owner_id),
        auto_update=read_auto_update(
            os.getenv("AUTO_UPDATE", ""), os.getenv("AUTO_UPDATE_CHECK", "")
        ),
        auto_update_minutes=read_minutes(os.getenv("AUTO_UPDATE_MINUTES", "")),
        polling_timeout=polling_timeout,
        proxy=read_proxy(os.getenv("TELEGRAM_PROXY", "")),
        env_file=env_file,
        env_lookalikes=find_lookalikes(env_file),
        log_path=os.getenv("LOG_FILE", "").strip()
        or os.path.join(
            os.path.dirname(os.getenv("DB_PATH", "data/diary.db").strip() or "data/diary.db")
            or "data",
            "bot.log",
        ),
        voice=VoiceConfig(
            binary=os.getenv("VOICE_BINARY", "").strip(),
            model=os.getenv("VOICE_MODEL", "").strip(),
            language=os.getenv("VOICE_LANGUAGE", "ru").strip() or "ru",
        ),
    )
