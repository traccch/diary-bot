"""Хранилище раздела «Давление»: измерения и показатели здоровья.

Время измерения хранится строкой «YYYY-MM-DD HH:MM» в часовом поясе
пользователя: дневник ведётся по местным часам, а такой формат сравнивается
лексикографически, поэтому выборка за период — обычный BETWEEN.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

import aiosqlite

STAMP_FORMAT = "%Y-%m-%d %H:%M"

SCHEMA = """
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

-- Показатели здоровья: сон, шаги, пульс покоя, вес. Одно значение на день,
-- повторная запись за тот же день заменяет предыдущую.
CREATE TABLE IF NOT EXISTS metrics (
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    on_date    TEXT NOT NULL,
    value      REAL NOT NULL,
    extra      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, kind, on_date)
);

CREATE INDEX IF NOT EXISTS idx_metrics_user_kind ON metrics(user_id, kind, on_date);
"""


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
class Metric:
    """Показатель здоровья за день: сон, шаги, пульс покоя или вес.

    `value` хранится в базовых единицах — сон в минутах, вес в килограммах.
    `extra` нужен сну: там лежит «23:21-07:01», чтобы показать режим, а не
    только длительность.
    """

    kind: str
    on_date: dt.date
    value: float
    extra: str = ""


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


def _row_to_metric(row: aiosqlite.Row) -> Metric:
    return Metric(
        kind=row["kind"],
        on_date=dt.date.fromisoformat(row["on_date"]),
        value=row["value"],
        extra=row["extra"],
    )


_SELECT = "SELECT id, systolic, diastolic, pulse, measured_at, note FROM measurements"


class PressureRepo:
    """Примесь к Database: запросы раздела «Давление»."""

    conn: aiosqlite.Connection

    # ----------------------------------------------------------- измерения

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

    async def has_measurement_since(self, user_id: int, since: dt.datetime) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM measurements WHERE user_id = ? AND measured_at >= ? LIMIT 1",
            (user_id, format_stamp(since)),
        )
        return await cur.fetchone() is not None

    # ------------------------------------------------- показатели здоровья

    async def set_metric(
        self, user_id: int, kind: str, on_date: dt.date, value: float, extra: str = ""
    ) -> Metric:
        """Записывает показатель за день, заменяя прежнее значение за ту же дату."""
        await self.conn.execute(
            "INSERT INTO metrics (user_id, kind, on_date, value, extra)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(user_id, kind, on_date) DO UPDATE SET"
            " value = excluded.value, extra = excluded.extra",
            (user_id, kind, on_date.isoformat(), float(value), extra),
        )
        await self.conn.commit()
        return Metric(kind=kind, on_date=on_date, value=float(value), extra=extra)

    async def get_metric(self, user_id: int, kind: str, on_date: dt.date) -> Optional[Metric]:
        cur = await self.conn.execute(
            "SELECT kind, on_date, value, extra FROM metrics"
            " WHERE user_id = ? AND kind = ? AND on_date = ?",
            (user_id, kind, on_date.isoformat()),
        )
        row = await cur.fetchone()
        return _row_to_metric(row) if row else None

    async def metrics_on(self, user_id: int, on_date: dt.date) -> set[str]:
        """Какие показатели за этот день уже записаны — чтобы не спрашивать дважды."""
        cur = await self.conn.execute(
            "SELECT kind FROM metrics WHERE user_id = ? AND on_date = ?",
            (user_id, on_date.isoformat()),
        )
        return {row["kind"] for row in await cur.fetchall()}

    async def metrics_between(
        self, user_id: int, kind: str, start: dt.date, end: dt.date
    ) -> list[Metric]:
        cur = await self.conn.execute(
            "SELECT kind, on_date, value, extra FROM metrics"
            " WHERE user_id = ? AND kind = ? AND on_date BETWEEN ? AND ?"
            " ORDER BY on_date",
            (user_id, kind, start.isoformat(), end.isoformat()),
        )
        return [_row_to_metric(row) for row in await cur.fetchall()]

    async def last_metric(self, user_id: int, kind: str) -> Optional[Metric]:
        cur = await self.conn.execute(
            "SELECT kind, on_date, value, extra FROM metrics"
            " WHERE user_id = ? AND kind = ? ORDER BY on_date DESC LIMIT 1",
            (user_id, kind),
        )
        row = await cur.fetchone()
        return _row_to_metric(row) if row else None

    async def delete_metric(self, user_id: int, kind: str, on_date: dt.date) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM metrics WHERE user_id = ? AND kind = ? AND on_date = ?",
            (user_id, kind, on_date.isoformat()),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def count_metrics(self, user_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM metrics WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return row["cnt"] if row else 0
