"""Слой доступа к данным поверх SQLite (aiosqlite).

Время измерения хранится строкой «YYYY-MM-DD HH:MM» в часовом поясе пользователя:
дневник ведётся «по местным часам», а такой формат сортируется и сравнивается
лексикографически, поэтому выборки за период — обычный BETWEEN.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from typing import Optional

import aiosqlite

#: Целевые значения по умолчанию — домашние измерения (ESC/ESH: АГ при ≥135/85).
DEFAULT_TARGET_SYS = 135
DEFAULT_TARGET_DIA = 85

STAMP_FORMAT = "%Y-%m-%d %H:%M"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id          INTEGER PRIMARY KEY,
    tz               TEXT NOT NULL DEFAULT 'Europe/Moscow',
    target_sys       INTEGER NOT NULL DEFAULT 135,
    target_dia       INTEGER NOT NULL DEFAULT 85,
    skip_if_measured INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS measurements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    systolic    INTEGER NOT NULL,
    diastolic   INTEGER NOT NULL,
    pulse       INTEGER,
    measured_at TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_measurements_user_time
    ON measurements(user_id, measured_at);

CREATE TABLE IF NOT EXISTS reminders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    at            TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_fired_on TEXT,
    UNIQUE(user_id, at)
);

CREATE TABLE IF NOT EXISTS snoozes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    fire_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class UserSettings:
    user_id: int
    tz: str
    target_sys: int = DEFAULT_TARGET_SYS
    target_dia: int = DEFAULT_TARGET_DIA
    skip_if_measured: bool = True


@dataclass(frozen=True)
class Measurement:
    id: int
    systolic: int
    diastolic: int
    pulse: Optional[int]
    measured_at: dt.datetime
    note: str = ""

    @property
    def bp(self) -> str:
        return f"{self.systolic}/{self.diastolic}"


@dataclass(frozen=True)
class Reminder:
    id: int
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
    at: dt.time
    last_fired_on: Optional[dt.date]
    skip_if_measured: bool


def parse_stamp(raw: str) -> dt.datetime:
    return dt.datetime.strptime(raw, STAMP_FORMAT)


def format_stamp(moment: dt.datetime) -> str:
    return moment.strftime(STAMP_FORMAT)


def _row_to_measurement(row: aiosqlite.Row) -> Measurement:
    return Measurement(
        id=row["id"],
        systolic=row["systolic"],
        diastolic=row["diastolic"],
        pulse=row["pulse"],
        measured_at=parse_stamp(row["measured_at"]),
        note=row["note"],
    )


def _row_to_reminder(row: aiosqlite.Row) -> Reminder:
    fired = row["last_fired_on"]
    return Reminder(
        id=row["id"],
        at=dt.time.fromisoformat(row["at"]),
        enabled=bool(row["enabled"]),
        last_fired_on=dt.date.fromisoformat(fired) if fired else None,
    )


_SELECT = "SELECT id, systolic, diastolic, pulse, measured_at, note FROM measurements"


class Database:
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
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ users

    async def ensure_user(self, user_id: int) -> UserSettings:
        cur = await self.conn.execute(
            "SELECT user_id, tz, target_sys, target_dia, skip_if_measured"
            " FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            await self.conn.execute(
                "INSERT INTO users (user_id, tz) VALUES (?, ?)", (user_id, self._default_tz)
            )
            await self.conn.commit()
            return UserSettings(user_id=user_id, tz=self._default_tz)
        return UserSettings(
            user_id=row["user_id"],
            tz=row["tz"],
            target_sys=row["target_sys"],
            target_dia=row["target_dia"],
            skip_if_measured=bool(row["skip_if_measured"]),
        )

    async def set_tz(self, user_id: int, tz: str) -> None:
        await self.conn.execute("UPDATE users SET tz = ? WHERE user_id = ?", (tz, user_id))
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

    # ----------------------------------------------------------- measurements

    async def add_measurement(
        self,
        user_id: int,
        systolic: int,
        diastolic: int,
        pulse: Optional[int],
        measured_at: dt.datetime,
        note: str = "",
    ) -> Measurement:
        cur = await self.conn.execute(
            "INSERT INTO measurements (user_id, systolic, diastolic, pulse, measured_at, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, systolic, diastolic, pulse, format_stamp(measured_at), note.strip()),
        )
        await self.conn.commit()
        created = await self.get_measurement(user_id, cur.lastrowid)
        assert created is not None
        return created

    async def get_measurement(self, user_id: int, measurement_id: int) -> Optional[Measurement]:
        cur = await self.conn.execute(
            _SELECT + " WHERE user_id = ? AND id = ?", (user_id, measurement_id)
        )
        row = await cur.fetchone()
        return _row_to_measurement(row) if row else None

    async def last_measurements(self, user_id: int, limit: int = 10) -> list[Measurement]:
        cur = await self.conn.execute(
            _SELECT + " WHERE user_id = ? ORDER BY measured_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        )
        return [_row_to_measurement(row) for row in await cur.fetchall()]

    async def measurements_between(
        self, user_id: int, start: dt.datetime, end: dt.datetime
    ) -> list[Measurement]:
        cur = await self.conn.execute(
            _SELECT + " WHERE user_id = ? AND measured_at BETWEEN ? AND ?"
            " ORDER BY measured_at, id",
            (user_id, format_stamp(start), format_stamp(end)),
        )
        return [_row_to_measurement(row) for row in await cur.fetchall()]

    async def delete_measurement(self, user_id: int, measurement_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM measurements WHERE user_id = ? AND id = ?", (user_id, measurement_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def set_note(
        self, user_id: int, measurement_id: int, note: str
    ) -> Optional[Measurement]:
        cur = await self.conn.execute(
            "UPDATE measurements SET note = ? WHERE user_id = ? AND id = ?",
            (note.strip(), user_id, measurement_id),
        )
        await self.conn.commit()
        if cur.rowcount == 0:
            return None
        return await self.get_measurement(user_id, measurement_id)

    async def first_measured_at(self, user_id: int) -> Optional[dt.datetime]:
        cur = await self.conn.execute(
            "SELECT MIN(measured_at) AS first FROM measurements WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return parse_stamp(row["first"]) if row and row["first"] else None

    async def count_measurements(self, user_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM measurements WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------ напоминания

    async def list_reminders(self, user_id: int) -> list[Reminder]:
        cur = await self.conn.execute(
            "SELECT id, at, enabled, last_fired_on FROM reminders"
            " WHERE user_id = ? ORDER BY at",
            (user_id,),
        )
        return [_row_to_reminder(row) for row in await cur.fetchall()]

    async def add_reminder(self, user_id: int, at: dt.time) -> Optional[Reminder]:
        """Добавляет напоминание. None, если на это время оно уже есть."""
        try:
            await self.conn.execute(
                "INSERT INTO reminders (user_id, at) VALUES (?, ?)",
                (user_id, at.strftime("%H:%M")),
            )
        except aiosqlite.IntegrityError:
            return None
        await self.conn.commit()
        for reminder in await self.list_reminders(user_id):
            if reminder.at == at:
                return reminder
        return None

    async def delete_reminder(self, user_id: int, at: dt.time) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM reminders WHERE user_id = ? AND at = ?",
            (user_id, at.strftime("%H:%M")),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def delete_all_reminders(self, user_id: int) -> int:
        cur = await self.conn.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))
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
            "SELECT r.id, r.user_id, r.at, r.last_fired_on, u.tz, u.skip_if_measured"
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
                    at=dt.time.fromisoformat(row["at"]),
                    last_fired_on=dt.date.fromisoformat(fired) if fired else None,
                    skip_if_measured=bool(row["skip_if_measured"]),
                )
            )
        return result

    async def has_measurement_since(self, user_id: int, since: dt.datetime) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM measurements WHERE user_id = ? AND measured_at >= ? LIMIT 1",
            (user_id, format_stamp(since)),
        )
        return await cur.fetchone() is not None

    # ------------------------------------------------------- отложенные (snooze)

    async def add_snooze(self, user_id: int, fire_at_utc: dt.datetime) -> None:
        await self.conn.execute(
            "INSERT INTO snoozes (user_id, fire_at) VALUES (?, ?)",
            (user_id, fire_at_utc.strftime("%Y-%m-%d %H:%M:%S")),
        )
        await self.conn.commit()

    async def pop_due_snoozes(self, now_utc: dt.datetime) -> list[int]:
        """Возвращает user_id, которым пора напомнить, и удаляет эти записи."""
        stamp = now_utc.strftime("%Y-%m-%d %H:%M:%S")
        cur = await self.conn.execute(
            "SELECT id, user_id FROM snoozes WHERE fire_at <= ?", (stamp,)
        )
        rows = await cur.fetchall()
        if not rows:
            return []
        await self.conn.executemany(
            "DELETE FROM snoozes WHERE id = ?", [(row["id"],) for row in rows]
        )
        await self.conn.commit()
        return [row["user_id"] for row in rows]
