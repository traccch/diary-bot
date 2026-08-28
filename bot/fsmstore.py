"""Состояние диалогов в базе, а не в памяти процесса.

Пошаговые разговоры — сессия английского, запись траты, квест — держат
состояние между сообщениями. Пока оно жило в памяти, любой перезапуск
(а бот обновляется сам) обрывал разговор на полуслове: человек отвечал на
седьмой вопрос из девяти, а бот уже не помнил ни вопроса, ни счёта.

Хранилище живёт в той же базе, что и дневник: отдельный файл здесь ничего
не улучшил бы, а забот прибавил.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS fsm_state (
    key        TEXT PRIMARY KEY,
    state      TEXT,
    data       TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

#: Дольше этого незаконченный разговор считается брошенным.
STALE_MINUTES = 30


def _key(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"


def _state_name(state: Optional[State | str]) -> Optional[str]:
    if state is None:
        return None
    return state.state if isinstance(state, State) else str(state)


class SQLiteStorage(BaseStorage):
    """Хранилище состояний aiogram поверх той же базы, что и дневник."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def set_state(self, key: StorageKey, state: Optional[State | str] = None) -> None:
        name = _state_name(state)
        if name is None:
            # состояние снято — данные без него не нужны никому
            await self._conn.execute(
                "UPDATE fsm_state SET state = NULL, updated_at = datetime('now')"
                " WHERE key = ?",
                (_key(key),),
            )
        else:
            await self._conn.execute(
                "INSERT INTO fsm_state (key, state) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET state = excluded.state,"
                " updated_at = datetime('now')",
                (_key(key), name),
            )
        await self._conn.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        cur = await self._conn.execute(
            "SELECT state FROM fsm_state WHERE key = ?", (_key(key),)
        )
        row = await cur.fetchone()
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._conn.execute(
            "INSERT INTO fsm_state (key, data) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET data = excluded.data,"
            " updated_at = datetime('now')",
            (_key(key), json.dumps(dict(data), ensure_ascii=False, default=str)),
        )
        await self._conn.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        cur = await self._conn.execute(
            "SELECT data FROM fsm_state WHERE key = ?", (_key(key),)
        )
        row = await cur.fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row["data"]) or {}
        except (TypeError, ValueError):
            logger.warning("Непонятные данные диалога, начинаю с чистого листа")
            return {}

    async def close(self) -> None:
        """Соединением владеет база дневника — закрывать его не наше дело."""

    # ---------------------------------------------------------- для бота

    async def busy(self, minutes: int = STALE_MINUTES) -> bool:
        """Есть ли прямо сейчас незаконченный разговор.

        По этому признаку обновление откладывается: перебивать человека
        посреди сессии — худшее, что может сделать фоновая задача.

        Время сравнивается целиком на стороне базы. Метки ставит SQLite, а он
        пишет UTC; питоновское `now()` вернуло бы местное время, и в поясе
        UTC+7 «полчаса назад» оказалось бы в будущем — незаконченных
        разговоров не находилось бы никогда.
        """
        cur = await self._conn.execute(
            "SELECT 1 FROM fsm_state WHERE state IS NOT NULL"
            f" AND updated_at >= datetime('now', '-{int(minutes)} minutes') LIMIT 1"
        )
        return await cur.fetchone() is not None

    async def forget_stale(self, minutes: int = STALE_MINUTES * 4) -> int:
        """Убирает разговоры, к которым никто не вернулся."""
        cur = await self._conn.execute(
            f"DELETE FROM fsm_state WHERE updated_at < datetime('now', '-{int(minutes)} minutes')"
        )
        await self._conn.commit()
        return cur.rowcount
