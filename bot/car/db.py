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

CREATE TABLE IF NOT EXISTS car_service (
    user_id     INTEGER PRIMARY KEY,
    due_km      INTEGER NOT NULL,
    interval_km INTEGER NOT NULL DEFAULT 0,
    done_km     INTEGER,
    set_on      TEXT NOT NULL DEFAULT (date('now'))
);
"""

#: Больше — это уже не одометр, а опечатка.
MAX_KM = 3_000_000


@dataclass(frozen=True)
class Reading:
    on_date: dt.date
    km: int


@dataclass(frozen=True)
class Service:
    """Ближайшее ТО: на каком пробеге и через сколько повторять."""

    due_km: int
    interval_km: int = 0
    done_km: Optional[int] = None

    def left(self, km: int) -> int:
        """Сколько осталось. Отрицательное — просрочено."""
        return self.due_km - km


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

    # ------------------------------------------------------------------- ТО

    async def set_service(
        self, user_id: int, due_km: int, interval_km: int = 0
    ) -> Service:
        await self.conn.execute(
            "INSERT INTO car_service (user_id, due_km, interval_km) VALUES (?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET due_km = excluded.due_km,"
            " interval_km = excluded.interval_km, set_on = date('now')",
            (user_id, int(due_km), int(interval_km)),
        )
        await self.conn.commit()
        return Service(int(due_km), int(interval_km))

    async def get_service(self, user_id: int) -> Optional[Service]:
        cur = await self.conn.execute(
            "SELECT due_km, interval_km, done_km FROM car_service WHERE user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return Service(row["due_km"], row["interval_km"], row["done_km"])

    async def complete_service(self, user_id: int, km: int) -> None:
        """Отмечает, что ТО сделано на этом пробеге."""
        await self.conn.execute(
            "UPDATE car_service SET done_km = ? WHERE user_id = ?", (int(km), user_id)
        )
        await self.conn.commit()

    async def clear_service(self, user_id: int) -> None:
        await self.conn.execute("DELETE FROM car_service WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def delete_reading(self, user_id: int, on_date: dt.date) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM car_readings WHERE user_id = ? AND on_date = ?",
            (user_id, on_date.isoformat()),
        )
        await self.conn.commit()
        return cur.rowcount > 0
