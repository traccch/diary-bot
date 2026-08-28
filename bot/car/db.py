"""Хранилище пробега: одно показание одометра на день.

Одометр только растёт, и по разнице между показаниями видно всё остальное:
сколько проехал за неделю, сколько выходит в день, во что обходится километр.
Поэтому храним не «проехал столько-то», а само число с приборной панели —
его нельзя ошибиться посчитать, только переписать.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS car_readings (
    user_id INTEGER NOT NULL,
    on_date TEXT NOT NULL,
    km      INTEGER NOT NULL,
    PRIMARY KEY (user_id, on_date)
);
"""

#: Больше — это уже не одометр, а опечатка.
MAX_KM = 3_000_000


@dataclass(frozen=True)
class Reading:
    on_date: dt.date
    km: int


def _row(row: aiosqlite.Row) -> Reading:
    return Reading(dt.date.fromisoformat(row["on_date"]), row["km"])


class CarRepo:
    """Подмешивается к Database — как разделы давления, денег и английского."""

    conn: aiosqlite.Connection

    async def set_reading(self, user_id: int, on_date: dt.date, km: int) -> Reading:
        await self.conn.execute(
            "INSERT INTO car_readings (user_id, on_date, km) VALUES (?, ?, ?)"
            " ON CONFLICT(user_id, on_date) DO UPDATE SET km = excluded.km",
            (user_id, on_date.isoformat(), int(km)),
        )
        await self.conn.commit()
        return Reading(on_date, int(km))

    async def reading_on(self, user_id: int, on_date: dt.date) -> Optional[Reading]:
        cur = await self.conn.execute(
            "SELECT on_date, km FROM car_readings WHERE user_id = ? AND on_date = ?",
            (user_id, on_date.isoformat()),
        )
        row = await cur.fetchone()
        return _row(row) if row else None

    async def last_reading(self, user_id: int) -> Optional[Reading]:
        cur = await self.conn.execute(
            "SELECT on_date, km FROM car_readings WHERE user_id = ?"
            " ORDER BY on_date DESC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return _row(row) if row else None

    async def readings_between(
        self, user_id: int, start: dt.date, end: dt.date
    ) -> list[Reading]:
        cur = await self.conn.execute(
            "SELECT on_date, km FROM car_readings"
            " WHERE user_id = ? AND on_date BETWEEN ? AND ? ORDER BY on_date",
            (user_id, start.isoformat(), end.isoformat()),
        )
        return [_row(row) for row in await cur.fetchall()]

    async def reading_before(
        self, user_id: int, on_date: dt.date
    ) -> Optional[Reading]:
        """Ближайшее показание до даты — начало отсчёта для периода."""
        cur = await self.conn.execute(
            "SELECT on_date, km FROM car_readings WHERE user_id = ? AND on_date < ?"
            " ORDER BY on_date DESC LIMIT 1",
            (user_id, on_date.isoformat()),
        )
        row = await cur.fetchone()
        return _row(row) if row else None

    async def count_readings(self, user_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM car_readings WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return row["cnt"] if row else 0

    async def delete_reading(self, user_id: int, on_date: dt.date) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM car_readings WHERE user_id = ? AND on_date = ?",
            (user_id, on_date.isoformat()),
        )
        await self.conn.commit()
        return cur.rowcount > 0
