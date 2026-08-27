"""Хранилище раздела «Английский»: прогресс по словам, сессии, квесты."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional, Sequence

from .srs import LEARNED_BOX, Progress

SCHEMA = """
-- Прогресс по каждому слову: коробка Лейтнера и дата следующего показа.
CREATE TABLE IF NOT EXISTS eng_progress (
    user_id  INTEGER NOT NULL,
    item_id  TEXT NOT NULL,
    box      INTEGER NOT NULL DEFAULT 0,
    due_on   TEXT NOT NULL,
    seen     INTEGER NOT NULL DEFAULT 0,
    correct  INTEGER NOT NULL DEFAULT 0,
    lapses   INTEGER NOT NULL DEFAULT 0,
    added_on TEXT NOT NULL DEFAULT (date('now')),
    PRIMARY KEY (user_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_eng_due ON eng_progress(user_id, due_on);

-- День занятий: сколько ответов и сколько верных. Одна строка на дату.
CREATE TABLE IF NOT EXISTS eng_days (
    user_id  INTEGER NOT NULL,
    on_date  TEXT NOT NULL,
    answered INTEGER NOT NULL DEFAULT 0,
    correct  INTEGER NOT NULL DEFAULT 0,
    new_seen INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, on_date)
);

-- Пройденные квесты.
CREATE TABLE IF NOT EXISTS eng_quests (
    user_id  INTEGER NOT NULL,
    quest_id TEXT NOT NULL,
    done_on  TEXT NOT NULL,
    score    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, quest_id)
);
"""


@dataclass(frozen=True)
class DayStats:
    on_date: dt.date
    answered: int
    correct: int
    new_seen: int


class EnglishRepo:
    """Подмешивается к Database — как разделы давления и денег."""

    async def eng_progress(self, user_id: int) -> list[Progress]:
        cur = await self.conn.execute(
            "SELECT item_id, box, due_on, seen, correct, lapses FROM eng_progress"
            " WHERE user_id = ?",
            (user_id,),
        )
        return [
            Progress(
                item_id=row["item_id"],
                box=row["box"],
                due_on=dt.date.fromisoformat(row["due_on"]),
                seen=row["seen"],
                correct=row["correct"],
                lapses=row["lapses"],
            )
            for row in await cur.fetchall()
        ]

    async def eng_progress_of(self, user_id: int, item_id: str) -> Optional[Progress]:
        cur = await self.conn.execute(
            "SELECT item_id, box, due_on, seen, correct, lapses FROM eng_progress"
            " WHERE user_id = ? AND item_id = ?",
            (user_id, item_id),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return Progress(
            item_id=row["item_id"],
            box=row["box"],
            due_on=dt.date.fromisoformat(row["due_on"]),
            seen=row["seen"],
            correct=row["correct"],
            lapses=row["lapses"],
        )

    async def eng_save_answer(
        self,
        user_id: int,
        item_id: str,
        box: int,
        due_on: dt.date,
        correct: bool,
        lapse: bool,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO eng_progress (user_id, item_id, box, due_on, seen, correct, lapses)"
            " VALUES (?, ?, ?, ?, 1, ?, ?)"
            " ON CONFLICT(user_id, item_id) DO UPDATE SET"
            "  box = excluded.box,"
            "  due_on = excluded.due_on,"
            "  seen = eng_progress.seen + 1,"
            "  correct = eng_progress.correct + excluded.correct,"
            "  lapses = eng_progress.lapses + excluded.lapses",
            (user_id, item_id, box, due_on.isoformat(), int(correct), int(lapse)),
        )
        await self.conn.commit()

    async def eng_bump_day(
        self, user_id: int, on_date: dt.date, correct: bool, is_new: bool
    ) -> None:
        await self.conn.execute(
            "INSERT INTO eng_days (user_id, on_date, answered, correct, new_seen)"
            " VALUES (?, ?, 1, ?, ?)"
            " ON CONFLICT(user_id, on_date) DO UPDATE SET"
            "  answered = eng_days.answered + 1,"
            "  correct = eng_days.correct + excluded.correct,"
            "  new_seen = eng_days.new_seen + excluded.new_seen",
            (user_id, on_date.isoformat(), int(correct), int(is_new)),
        )
        await self.conn.commit()

    async def eng_day(self, user_id: int, on_date: dt.date) -> DayStats:
        cur = await self.conn.execute(
            "SELECT answered, correct, new_seen FROM eng_days"
            " WHERE user_id = ? AND on_date = ?",
            (user_id, on_date.isoformat()),
        )
        row = await cur.fetchone()
        if row is None:
            return DayStats(on_date, 0, 0, 0)
        return DayStats(on_date, row["answered"], row["correct"], row["new_seen"])

    async def eng_active_days(self, user_id: int, limit: int = 400) -> list[dt.date]:
        """Дни с занятиями, от свежих к старым — по ним считается серия."""
        cur = await self.conn.execute(
            "SELECT on_date FROM eng_days WHERE user_id = ? AND answered > 0"
            " ORDER BY on_date DESC LIMIT ?",
            (user_id, limit),
        )
        return [dt.date.fromisoformat(row["on_date"]) for row in await cur.fetchall()]

    async def eng_counts(self, user_id: int) -> tuple[int, int]:
        """Сколько слов в работе и сколько уже выучено."""
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN box >= ? THEN 1 ELSE 0 END) AS learned"
            " FROM eng_progress WHERE user_id = ?",
            (LEARNED_BOX, user_id),
        )
        row = await cur.fetchone()
        return (row["total"] or 0), (row["learned"] or 0)

    async def eng_due_count(self, user_id: int, today: dt.date) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS due FROM eng_progress WHERE user_id = ? AND due_on <= ?",
            (user_id, today.isoformat()),
        )
        row = await cur.fetchone()
        return row["due"] or 0

    async def eng_finish_quest(
        self, user_id: int, quest_id: str, on_date: dt.date, score: int
    ) -> None:
        await self.conn.execute(
            "INSERT INTO eng_quests (user_id, quest_id, done_on, score) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(user_id, quest_id) DO UPDATE SET"
            "  done_on = excluded.done_on, score = max(eng_quests.score, excluded.score)",
            (user_id, quest_id, on_date.isoformat(), score),
        )
        await self.conn.commit()

    async def eng_done_quests(self, user_id: int) -> list[str]:
        cur = await self.conn.execute(
            "SELECT quest_id FROM eng_quests WHERE user_id = ? ORDER BY done_on", (user_id,)
        )
        return [row["quest_id"] for row in await cur.fetchall()]

    async def eng_practiced_since(self, user_id: int, since: dt.date) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM eng_days WHERE user_id = ? AND on_date >= ? AND answered > 0"
            " LIMIT 1",
            (user_id, since.isoformat()),
        )
        return await cur.fetchone() is not None


def streak(days: Sequence[dt.date], today: dt.date) -> int:
    """Сколько дней подряд были занятия. Сегодняшний пропуск ещё не рвёт серию."""
    if not days:
        return 0
    ordered = sorted(set(days), reverse=True)
    start = ordered[0]
    if (today - start).days > 1:
        return 0

    count = 1
    for previous in ordered[1:]:
        if (start - previous).days == 1:
            count += 1
            start = previous
        else:
            break
    return count
