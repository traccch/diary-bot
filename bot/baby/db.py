"""Хранилище раздела «Малыш»: ребёнок, кто к нему допущен, сон и замеры.

Ребёнок общий: записи видят и пополняют все его взрослые — и хозяин бота,
и тот, кого он пригласил. Приглашённый («семья») видит только этот раздел:
ни английского, ни давления, ни денег хозяина ему не показывают.
"""

from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass
from typing import Optional

from .sleep import Sleep

OWNER = "owner"
FAMILY = "family"

SCHEMA = """
CREATE TABLE IF NOT EXISTS baby_children (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    sex        TEXT NOT NULL DEFAULT 'm',
    birth_date TEXT NOT NULL,
    city       TEXT NOT NULL DEFAULT '',
    lat        REAL,
    lon        REAL,
    created_by INTEGER NOT NULL
);

-- Кто допущен к ребёнку: хозяин (owner) или приглашённый (family).
CREATE TABLE IF NOT EXISTS baby_members (
    user_id  INTEGER PRIMARY KEY,
    child_id INTEGER NOT NULL,
    role     TEXT NOT NULL DEFAULT 'family'
);

-- Сон: начало и конец в местном времени «YYYY-MM-DD HH:MM»; конец пуст,
-- пока малыш спит.
CREATE TABLE IF NOT EXISTS baby_sleep (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    start_at TEXT NOT NULL,
    end_at   TEXT,
    by_user  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_baby_sleep ON baby_sleep(child_id, start_at);

-- Замеры у педиатра: по строке на показатель (вес, рост, голова).
CREATE TABLE IF NOT EXISTS baby_growth (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id INTEGER NOT NULL,
    on_date  TEXT NOT NULL,
    kind     TEXT NOT NULL,
    value    REAL NOT NULL,
    by_user  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_baby_growth ON baby_growth(child_id, kind, on_date);
"""

_FORMAT = "%Y-%m-%d %H:%M"


def _stamp(moment: dt.datetime) -> str:
    return moment.strftime(_FORMAT)


def _parse(value: Optional[str]) -> Optional[dt.datetime]:
    return dt.datetime.strptime(value, _FORMAT) if value else None


@dataclass(frozen=True)
class Child:
    id: int
    name: str
    sex: str
    birth: dt.date
    city: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None


def _child(row) -> Child:
    return Child(
        id=row["id"],
        name=row["name"],
        sex=row["sex"],
        birth=dt.date.fromisoformat(row["birth_date"]),
        city=row["city"] or "",
        lat=row["lat"],
        lon=row["lon"],
    )


def _sleep(row) -> Sleep:
    return Sleep(id=row["id"], start=_parse(row["start_at"]), end=_parse(row["end_at"]))


def invite_key(code: str) -> str:
    return f"baby_invite:{code}"


class BabyRepo:
    """Подмешивается к Database."""

    # ------------------------------------------------------------- ребёнок

    async def baby_of(self, user_id: int) -> tuple[Optional[Child], Optional[str]]:
        """Ребёнок, к которому допущен человек, и его роль."""
        cur = await self.conn.execute(
            "SELECT c.*, m.role FROM baby_members m JOIN baby_children c ON c.id = m.child_id"
            " WHERE m.user_id = ?",
            (user_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None, None
        return _child(row), row["role"]

    async def baby_role(self, user_id: int) -> Optional[str]:
        cur = await self.conn.execute(
            "SELECT role FROM baby_members WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return row["role"] if row else None

    async def baby_create(
        self, user_id: int, name: str, sex: str, birth: dt.date
    ) -> Child:
        cur = await self.conn.execute(
            "INSERT INTO baby_children (name, sex, birth_date, created_by) VALUES (?, ?, ?, ?)",
            (name, sex, birth.isoformat(), user_id),
        )
        child_id = cur.lastrowid
        await self.conn.execute(
            "INSERT OR REPLACE INTO baby_members (user_id, child_id, role) VALUES (?, ?, ?)",
            (user_id, child_id, OWNER),
        )
        await self.conn.commit()
        return Child(child_id, name, sex, birth)

    async def baby_update(self, child_id: int, **fields) -> None:
        allowed = {"name", "sex", "birth_date", "city", "lat", "lon"}
        pairs = [(key, value) for key, value in fields.items() if key in allowed]
        if not pairs:
            return
        sql = ", ".join(f"{key} = ?" for key, _ in pairs)
        await self.conn.execute(
            f"UPDATE baby_children SET {sql} WHERE id = ?",
            [value for _, value in pairs] + [child_id],
        )
        await self.conn.commit()

    async def baby_children_with_city(self) -> list[Child]:
        cur = await self.conn.execute("SELECT * FROM baby_children WHERE lat IS NOT NULL")
        return [_child(row) for row in await cur.fetchall()]

    # ------------------------------------------------------- приглашения

    async def baby_invite(self, child_id: int) -> str:
        """Одноразовый код для ссылки-приглашения."""
        code = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")
        await self.set_meta(invite_key(code), str(child_id))
        return code

    async def baby_invite_valid(self, code: str) -> bool:
        return bool(code) and bool(await self.get_meta(invite_key(code)))

    async def baby_join(self, user_id: int, code: str) -> Optional[Child]:
        """Принимает приглашение. Код сгорает: второй раз по ссылке не войти."""
        stored = await self.get_meta(invite_key(code))
        if not stored:
            return None
        await self.set_meta(invite_key(code), "")
        role = await self.baby_role(user_id)
        if role == OWNER:
            child, _ = await self.baby_of(user_id)
            return child
        await self.conn.execute(
            "INSERT OR REPLACE INTO baby_members (user_id, child_id, role) VALUES (?, ?, ?)",
            (user_id, int(stored), FAMILY),
        )
        # напоминания про давление и английский приглашённому ни к чему
        await self.conn.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))
        await self.conn.commit()
        child, _ = await self.baby_of(user_id)
        return child

    async def baby_members(self, child_id: int) -> list[tuple[int, str]]:
        cur = await self.conn.execute(
            "SELECT user_id, role FROM baby_members WHERE child_id = ? ORDER BY role DESC, user_id",
            (child_id,),
        )
        return [(row["user_id"], row["role"]) for row in await cur.fetchall()]

    async def baby_remove_member(self, child_id: int, user_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM baby_members WHERE child_id = ? AND user_id = ? AND role = ?",
            (child_id, user_id, FAMILY),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # ---------------------------------------------------------------- сон

    async def baby_open_sleep(self, child_id: int) -> Optional[Sleep]:
        cur = await self.conn.execute(
            "SELECT * FROM baby_sleep WHERE child_id = ? AND end_at IS NULL"
            " ORDER BY start_at DESC LIMIT 1",
            (child_id,),
        )
        row = await cur.fetchone()
        return _sleep(row) if row else None

    async def baby_start_sleep(self, child_id: int, at: dt.datetime, by: int) -> Sleep:
        cur = await self.conn.execute(
            "INSERT INTO baby_sleep (child_id, start_at, by_user) VALUES (?, ?, ?)",
            (child_id, _stamp(at), by),
        )
        await self.conn.commit()
        return Sleep(cur.lastrowid, at.replace(second=0, microsecond=0), None)

    async def baby_end_sleep(self, sleep_id: int, at: dt.datetime) -> None:
        await self.conn.execute(
            "UPDATE baby_sleep SET end_at = ? WHERE id = ?", (_stamp(at), sleep_id)
        )
        await self.conn.commit()

    async def baby_add_sleep(
        self, child_id: int, start: dt.datetime, end: dt.datetime, by: int
    ) -> Sleep:
        cur = await self.conn.execute(
            "INSERT INTO baby_sleep (child_id, start_at, end_at, by_user) VALUES (?, ?, ?, ?)",
            (child_id, _stamp(start), _stamp(end), by),
        )
        await self.conn.commit()
        return Sleep(cur.lastrowid, start, end)

    async def baby_sleeps(self, child_id: int, since: dt.datetime) -> list[Sleep]:
        """Сны, которые закончились после `since` или ещё идут."""
        cur = await self.conn.execute(
            "SELECT * FROM baby_sleep WHERE child_id = ?"
            " AND (end_at IS NULL OR end_at >= ?) ORDER BY start_at",
            (child_id, _stamp(since)),
        )
        return [_sleep(row) for row in await cur.fetchall()]

    async def baby_last_wake(self, child_id: int) -> Optional[dt.datetime]:
        cur = await self.conn.execute(
            "SELECT end_at FROM baby_sleep WHERE child_id = ? AND end_at IS NOT NULL"
            " ORDER BY end_at DESC LIMIT 1",
            (child_id,),
        )
        row = await cur.fetchone()
        return _parse(row["end_at"]) if row else None

    async def baby_last_sleep(self, child_id: int) -> Optional[Sleep]:
        cur = await self.conn.execute(
            "SELECT * FROM baby_sleep WHERE child_id = ? ORDER BY id DESC LIMIT 1", (child_id,)
        )
        row = await cur.fetchone()
        return _sleep(row) if row else None

    async def baby_delete_sleep(self, child_id: int, sleep_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM baby_sleep WHERE child_id = ? AND id = ?", (child_id, sleep_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # -------------------------------------------------------------- замеры

    async def baby_add_growth(
        self, child_id: int, on: dt.date, values: dict, by: int
    ) -> None:
        for kind, value in values.items():
            await self.conn.execute(
                "DELETE FROM baby_growth WHERE child_id = ? AND kind = ? AND on_date = ?",
                (child_id, kind, on.isoformat()),
            )
            await self.conn.execute(
                "INSERT INTO baby_growth (child_id, on_date, kind, value, by_user)"
                " VALUES (?, ?, ?, ?, ?)",
                (child_id, on.isoformat(), kind, float(value), by),
            )
        await self.conn.commit()

    async def baby_growth(self, child_id: int) -> list[tuple[dt.date, str, float]]:
        cur = await self.conn.execute(
            "SELECT on_date, kind, value FROM baby_growth WHERE child_id = ? ORDER BY on_date",
            (child_id,),
        )
        return [
            (dt.date.fromisoformat(row["on_date"]), row["kind"], row["value"])
            for row in await cur.fetchall()
        ]
