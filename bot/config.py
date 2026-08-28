"""Загрузка конфигурации из переменных окружения / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

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
    auto_update_check: bool
    #: Сколько секунд держать запрос обновлений открытым. Через фильтрующие
    #: сети длинный запрос обрывают на полуслове, поэтому по умолчанию короче
    #: аиограмовских 30 секунд.
    polling_timeout: int
    #: Через какой прокси ходить к Telegram. Пусто — напрямую.
    proxy: str
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


def read_proxy(raw: str) -> str:
    """Проверяет адрес прокси. Непонятное — лучше отвергнуть громко."""
    value = raw.strip().strip('"').strip("'")
    if not value:
        return ""
    lowered = value.lower()
    if lowered.startswith(PROXY_SCHEMES):
        return value
    if lowered.startswith(VPN_KEY_SCHEMES):
        raise RuntimeError(PROXY_HELP.format(value=value[:24] + "…"))
    if ":" in value and "//" not in value:
        # «127.0.0.1:2080» — понятно, что имелось в виду
        return "socks5://" + value
    raise RuntimeError(PROXY_HELP.format(value=value[:40]))


def load_config() -> Config:
    load_dotenv()

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
        auto_update_check=os.getenv("AUTO_UPDATE_CHECK", "1").strip().lower()
        not in {"0", "false", "no", "off"},
        polling_timeout=polling_timeout,
        proxy=read_proxy(os.getenv("TELEGRAM_PROXY", "")),
        voice=VoiceConfig(
            binary=os.getenv("VOICE_BINARY", "").strip(),
            model=os.getenv("VOICE_MODEL", "").strip(),
            language=os.getenv("VOICE_LANGUAGE", "ru").strip() or "ru",
        ),
    )
