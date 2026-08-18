"""Загрузка конфигурации из переменных окружения / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


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

    raw_owner = os.getenv("OWNER_ID", "").strip()
    owner_id = int(raw_owner) if raw_owner.lstrip("-").isdigit() else None

    return Config(
        token=token,
        db_path=os.getenv("DB_PATH", "data/pressure.db").strip() or "data/pressure.db",
        default_tz=tz,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        owner_id=owner_id,
        auto_update_check=os.getenv("AUTO_UPDATE_CHECK", "1").strip().lower()
        not in {"0", "false", "no", "off"},
    )
