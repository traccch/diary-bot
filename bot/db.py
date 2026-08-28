"""Ядро хранилища: подключение к SQLite, пользователи, напоминания, пометки.

Запросы разделов живут рядом со своими модулями и подмешиваются к Database:
`bot/pressure/db.py` и `bot/money/db.py`. Так каждый раздел можно читать
целиком, не листая общий файл на тысячу строк.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import aiosqlite

from . import sections
from .car.db import SCHEMA as CAR_SCHEMA
from .car.db import CarRepo
from .english.db import SCHEMA as ENGLISH_SCHEMA
from .english.db import EnglishRepo
from .money.db import SCHEMA as MONEY_SCHEMA
from .money.db import MoneyRepo
from .pressure.db import SCHEMA as PRESSURE_SCHEMA
from .pressure.db import PressureRepo

logger = logging.getLogger(__name__)

#: Напоминания, которые появляются сами — без единой настройки.
#: Привычка не заводится с чистого листа: если бот молчит, пока его не
#: попросили, о нём просто забывают. Выключить можно всегда — /remind off,
#: и повторно они уже не насаждаются.
DEFAULT_REMINDERS: dict[str, tuple[str, ...]] = {
    sections.PRESSURE: ("08:00", "21:00"),
    sections.MONEY: ("21:30",),
    sections.ENGLISH: ("13:00", "19:30"),
    sections.HEALTH: ("09:30", "22:00"),
    sections.CAR: ("07:30",),
}

#: Целевые значения по умолчанию — домашние измерения (ESC/ESH: АГ при ≥135/85).
DEFAULT_TARGET_SYS = 135
DEFAULT_TARGET_DIA = 85

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id          INTEGER PRIMARY KEY,
    tz               TEXT NOT NULL DEFAULT 'Europe/Moscow',
    target_sys       INTEGER NOT NULL DEFAULT 135,
    target_dia       INTEGER NOT NULL DEFAULT 85,
    skip_if_measured INTEGER NOT NULL DEFAULT 1,
    section          TEXT NOT NULL DEFAULT 'pressure',
    currency         TEXT NOT NULL DEFAULT '₽',
    reminders_seeded INTEGER NOT NULL DEFAULT 0,
    seeded_topics    TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reminders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    topic         TEXT NOT NULL DEFAULT 'pressure',
    at            TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_fired_on TEXT,
    UNIQUE(user_id, topic, at)
);

CREATE TABLE IF NOT EXISTS snoozes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    topic   TEXT NOT NULL DEFAULT 'pressure',
    fire_at TEXT NOT NULL
);

-- Служебные пометки бота: например, о каком обновлении владельцу уже сказали.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SCHEMA = CORE_SCHEMA + PRESSURE_SCHEMA + MONEY_SCHEMA + ENGLISH_SCHEMA + CAR_SCHEMA


_CREATE_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", re.DOTALL)
#: Строки описания таблицы, которые не являются колонками.
_NOT_A_COLUMN = frozenset({"PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"})


def expected_columns(schema: str) -> dict[str, list[tuple[str, str]]]:
    """Разбирает SCHEMA в {таблица: [(колонка, описание), …]}."""
    tables: dict[str, list[tuple[str, str]]] = {}
    for table, body in _CREATE_TABLE.findall(schema):
        columns: list[tuple[str, str]] = []
        for line in body.splitlines():
            line = line.strip().rstrip(",").strip()
            if not line or line.startswith("--"):
                continue
            # «UNIQUE(user_id, at)» пишут и без пробела перед скобкой
            if line.split()[0].split("(")[0].upper() in _NOT_A_COLUMN:
                continue
            column, _, definition = line.partition(" ")
            columns.append((column, definition.strip()))
        tables[table] = columns
    return tables


@dataclass(frozen=True)
class UserSettings:
    user_id: int
    tz: str
    target_sys: int = DEFAULT_TARGET_SYS
    target_dia: int = DEFAULT_TARGET_DIA
    skip_if_measured: bool = True
    section: str = sections.DEFAULT
    currency: str = "₽"


@dataclass(frozen=True)
class Reminder:
    id: int
    topic: str
    at: dt.time
    enabled: bool
    last_fired_on: Optional[dt.date]

    @property
    def label(self) -> str:
        return self.at.strftime("%H:%M")


@dataclass(frozen=True)
class DueReminder:
    """Напоминание, готовое к отправке: с часовым поясом владельца."""

    user_id: int
    tz: str
    reminder_id: int
    topic: str
    at: dt.time
    last_fired_on: Optional[dt.date]
    skip_if_measured: bool


def _row_to_reminder(row: aiosqlite.Row) -> Reminder:
    fired = row["last_fired_on"]
    return Reminder(
        id=row["id"],
        topic=row["topic"],
        at=dt.time.fromisoformat(row["at"]),
        enabled=bool(row["enabled"]),
        last_fired_on=dt.date.fromisoformat(fired) if fired else None,
    )


class Database(PressureRepo, MoneyRepo, EnglishRepo, CarRepo):
    def __init__(self, path: str, default_tz: str) -> None:
        self._path = path
        self._default_tz = default_tz
        self._conn: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------ setup

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() не был вызван")
        return self._conn

    async def connect(self) -> None:
        directory = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(directory, exist_ok=True)
        # базы ещё нет — значит, и дополнять в ней нечего: всё создаст схема
        fresh = self._path == ":memory:" or not os.path.exists(self._path)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        if not fresh:
            await self._migrate()
        await self.sync_money_categories()
        await self.catch_up_fuel()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Дополняет уже существующую базу колонками, появившимися в SCHEMA позже.

        Новые таблицы создаёт сам `CREATE TABLE IF NOT EXISTS`, а вот колонка,
        добавленная в описание существующей таблицы, до старой базы не доедет —
        и бот падает с «no such column» на дневнике, который уже жалко потерять.
        Список ожидаемых колонок берётся из самой SCHEMA, поэтому за ним не надо
        следить руками.
        """
        for table, columns in expected_columns(SCHEMA).items():
            cur = await self.conn.execute(f"PRAGMA table_info({table})")
            existing = {row["name"] for row in await cur.fetchall()}
            if not existing:
                continue  # таблицы не было — её только что создал executescript

            for column, definition in columns:
                if column in existing:
                    continue
                try:
                    await self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                    )
                except aiosqlite.OperationalError as exc:
                    # SQLite не умеет добавлять колонку с неконстантным DEFAULT
                    logger.warning("Не смог добавить %s.%s: %s", table, column, exc)
                else:
                    logger.info("Добавил колонку %s.%s", table, column)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ users

    async def ensure_user(self, user_id: int) -> UserSettings:
        cur = await self.conn.execute(
            "SELECT user_id, tz, target_sys, target_dia, skip_if_measured, section,"
            " currency, reminders_seeded FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                "INSERT INTO users (user_id, tz) VALUES (?, ?)", (user_id, self._default_tz)
            )
            await self.conn.commit()
            await self.seed_money_categories(user_id)
            await self.seed_default_reminders(user_id)
            return UserSettings(user_id=user_id, tz=self._default_tz)

        await self.seed_default_reminders(user_id)
        return UserSettings(
            user_id=row["user_id"],
            tz=row["tz"],
            target_sys=row["target_sys"],
            target_dia=row["target_dia"],
            skip_if_measured=bool(row["skip_if_measured"]),
            section=row["section"],
            currency=row["currency"],
        )

    async def seed_default_reminders(self, user_id: int) -> int:
        """Ставит напоминания по умолчанию — по каждой теме ровно один раз.

        Раньше отметка была одна на всего человека, и тема, появившаяся в
        новой версии бота, до старого дневника не доезжала: он был «уже
        размечен». Теперь помним темы поимённо — новая приедет, выключенная
        не вернётся.
        """
        seeded = await self._seeded_topics(user_id)
        rows = [
            (user_id, topic, at)
            for topic, times in DEFAULT_REMINDERS.items()
            if topic not in seeded
            for at in times
        ]
        if not rows:
            return 0

        await self.conn.executemany(
            "INSERT OR IGNORE INTO reminders (user_id, topic, at) VALUES (?, ?, ?)", rows
        )
        await self.conn.execute(
            "UPDATE users SET reminders_seeded = 1, seeded_topics = ? WHERE user_id = ?",
            (",".join(sorted(seeded | set(DEFAULT_REMINDERS))), user_id),
        )
        await self.conn.commit()
        logger.info("Добавил напоминания по умолчанию: %s", len(rows))
        return len(rows)

    async def _seeded_topics(self, user_id: int) -> set[str]:
        """Темы, по которым умолчания уже предлагались.

        У дневников, заведённых до появления этого списка, считаем
        размеченным то, по чему напоминания есть сейчас: значит, их уже
        ставили, а что удалено — то удалено осознанно.
        """
        cur = await self.conn.execute(
            "SELECT seeded_topics, reminders_seeded FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return set()
        stored = {item for item in (row["seeded_topics"] or "").split(",") if item}
        if stored or not row["reminders_seeded"]:
            return stored

        cur = await self.conn.execute(
            "SELECT DISTINCT topic FROM reminders WHERE user_id = ?", (user_id,)
        )
        return {item["topic"] for item in await cur.fetchall()}

    async def catch_up_fuel(self) -> int:
        """Разово вытаскивает заправки из трат, записанных до появления литров."""
        if await self.get_meta("fuel_imported"):
            return 0
        cur = await self.conn.execute("SELECT user_id FROM users")
        found = 0
        for row in await cur.fetchall():
            found += await self.import_fuel_from_notes(row["user_id"])
        await self.set_meta("fuel_imported", dt.date.today().isoformat())
        if found:
            logger.info("Нашёл заправок в старых записях: %s", found)
        return found

    async def owner_id(self) -> Optional[int]:
        """Хозяин бота — тот, кто написал ему первым."""
        cur = await self.conn.execute(
            "SELECT user_id FROM users ORDER BY created_at, user_id LIMIT 1"
        )
        row = await cur.fetchone()
        return row["user_id"] if row else None

    async def get_meta(self, key: str) -> Optional[str]:
        cur = await self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()

    async def set_tz(self, user_id: int, tz: str) -> None:
        await self.conn.execute("UPDATE users SET tz = ? WHERE user_id = ?", (tz, user_id))
        await self.conn.commit()

    async def set_section(self, user_id: int, section: str) -> None:
        await self.conn.execute(
            "UPDATE users SET section = ? WHERE user_id = ?", (section, user_id)
        )
        await self.conn.commit()

    async def set_currency(self, user_id: int, currency: str) -> None:
        await self.conn.execute(
            "UPDATE users SET currency = ? WHERE user_id = ?", (currency, user_id)
        )
        await self.conn.commit()

    async def set_target(self, user_id: int, systolic: int, diastolic: int) -> None:
        await self.conn.execute(
            "UPDATE users SET target_sys = ?, target_dia = ? WHERE user_id = ?",
            (systolic, diastolic, user_id),
        )
        await self.conn.commit()

    async def set_skip_if_measured(self, user_id: int, value: bool) -> None:
        await self.conn.execute(
            "UPDATE users SET skip_if_measured = ? WHERE user_id = ?", (int(value), user_id)
        )
        await self.conn.commit()

    # ------------------------------------------------------------ напоминания

    async def list_reminders(
        self, user_id: int, topic: Optional[str] = None
    ) -> list[Reminder]:
        query = "SELECT id, topic, at, enabled, last_fired_on FROM reminders WHERE user_id = ?"
        params: list = [user_id]
        if topic is not None:
            query += " AND topic = ?"
            params.append(topic)
        cur = await self.conn.execute(query + " ORDER BY topic, at", params)
        return [_row_to_reminder(row) for row in await cur.fetchall()]

    async def add_reminder(
        self, user_id: int, at: dt.time, topic: str = sections.PRESSURE
    ) -> Optional[Reminder]:
        """Добавляет напоминание. None, если на это время оно уже есть."""
        try:
            await self.conn.execute(
                "INSERT INTO reminders (user_id, topic, at) VALUES (?, ?, ?)",
                (user_id, topic, at.strftime("%H:%M")),
            )
        except aiosqlite.IntegrityError:
            return None
        await self.conn.commit()
        for reminder in await self.list_reminders(user_id, topic):
            if reminder.at == at:
                return reminder
        return None

    async def delete_reminder(
        self, user_id: int, at: dt.time, topic: str = sections.PRESSURE
    ) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM reminders WHERE user_id = ? AND topic = ? AND at = ?",
            (user_id, topic, at.strftime("%H:%M")),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def delete_all_reminders(
        self, user_id: int, topic: Optional[str] = None
    ) -> int:
        query = "DELETE FROM reminders WHERE user_id = ?"
        params: list = [user_id]
        if topic is not None:
            query += " AND topic = ?"
            params.append(topic)
        cur = await self.conn.execute(query, params)
        await self.conn.commit()
        return cur.rowcount

    async def mark_reminder_fired(self, reminder_id: int, on: dt.date) -> None:
        await self.conn.execute(
            "UPDATE reminders SET last_fired_on = ? WHERE id = ?", (on.isoformat(), reminder_id)
        )
        await self.conn.commit()

    async def due_candidates(self) -> list[DueReminder]:
        """Все включённые напоминания вместе с настройками владельца."""
        cur = await self.conn.execute(
            "SELECT r.id, r.user_id, r.topic, r.at, r.last_fired_on, u.tz, u.skip_if_measured"
            " FROM reminders r JOIN users u ON u.user_id = r.user_id"
            " WHERE r.enabled = 1"
        )
        result = []
        for row in await cur.fetchall():
            fired = row["last_fired_on"]
            result.append(
                DueReminder(
                    user_id=row["user_id"],
                    tz=row["tz"],
                    reminder_id=row["id"],
                    topic=row["topic"],
                    at=dt.time.fromisoformat(row["at"]),
                    last_fired_on=dt.date.fromisoformat(fired) if fired else None,
                    skip_if_measured=bool(row["skip_if_measured"]),
                )
            )
        return result

    # ------------------------------------------------------- отложенные (snooze)

    async def add_snooze(
        self, user_id: int, fire_at_utc: dt.datetime, topic: str = sections.PRESSURE
    ) -> None:
        await self.conn.execute(
            "INSERT INTO snoozes (user_id, topic, fire_at) VALUES (?, ?, ?)",
            (user_id, topic, fire_at_utc.strftime("%Y-%m-%d %H:%M:%S")),
        )
        await self.conn.commit()

    async def pop_due_snoozes(self, now_utc: dt.datetime) -> list[tuple[int, str]]:
        """Возвращает (user_id, тема) для сработавших отсрочек и удаляет их."""
        stamp = now_utc.strftime("%Y-%m-%d %H:%M:%S")
        cur = await self.conn.execute(
            "SELECT id, user_id, topic FROM snoozes WHERE fire_at <= ?", (stamp,)
        )
        rows = await cur.fetchall()
        if not rows:
            return []
        await self.conn.executemany(
            "DELETE FROM snoozes WHERE id = ?", [(row["id"],) for row in rows]
        )
        await self.conn.commit()
        return [(row["user_id"], row["topic"]) for row in rows]
