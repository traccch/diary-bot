"""Хранилище раздела «Деньги»: операции, категории, лимиты.

Суммы хранятся в минорных единицах (копейках) целым числом, чтобы не ловить
ошибки округления float. Доход и расход лежат в одной таблице и отличаются
полем kind — так проще считать баланс и не заводить два почти одинаковых
набора запросов.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional, Sequence

import aiosqlite

#: Виды операций.
EXPENSE = "expense"
INCOME = "income"

#: category_id, под которым хранится общий (не привязанный к категории) лимит
TOTAL_LIMIT_CATEGORY = 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS money_categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'expense',
    name        TEXT NOT NULL,
    emoji       TEXT NOT NULL DEFAULT '📦',
    keywords    TEXT NOT NULL DEFAULT '',
    is_fallback INTEGER NOT NULL DEFAULT 0,
    is_transfer INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, kind, name)
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'expense',
    amount      INTEGER NOT NULL,
    category_id INTEGER REFERENCES money_categories(id) ON DELETE SET NULL,
    note        TEXT NOT NULL DEFAULT '',
    happened_on TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_date
    ON transactions(user_id, happened_on);

CREATE TABLE IF NOT EXISTS money_limits (
    user_id     INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    amount      INTEGER NOT NULL,
    PRIMARY KEY (user_id, category_id)
);
"""

#: Категории-«перекладывания»: деньги не потрачены и не заработаны, а
#: переложены из кармана в карман. Отложенное вернётся, занятое придётся
#: отдать — считать это тратой значит обманывать себя в обе стороны.
TRANSFER_CATEGORIES = frozenset({"Долги", "Накопления"})

#: (эмодзи, название, ключевые слова) — расходные категории
DEFAULT_EXPENSE_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("🍔", "Продукты", ("продукты", "еда", "магазин", "супермаркет", "пятерочка",
                        "пятёрочка", "перекресток", "перекрёсток", "магнит", "лента",
                        "ашан", "вкусвилл", "молоко", "хлеб")),
    ("☕", "Кафе", ("кофе", "кафе", "ресторан", "обед", "ужин", "завтрак", "бар",
                    "пиво", "доставка", "пицца", "суши", "бургер", "столовая")),
    ("🚕", "Транспорт", ("такси", "метро", "автобус", "трамвай", "маршрутка",
                         "бензин", "заправка", "транспорт", "поезд", "самолет",
                         "самолёт", "каршеринг", "самокат", "парковка")),
    ("🏠", "Жильё", ("аренда", "квартира", "жкх", "коммуналка", "интернет",
                     "электричество", "вода", "ипотека")),
    ("🛍", "Покупки", ("одежда", "обувь", "техника", "покупки", "маркетплейс",
                       "озон", "ozon", "вб", "wildberries", "днс", "икеа")),
    ("💊", "Здоровье", ("аптека", "врач", "лекарства", "стоматолог", "анализы",
                        "здоровье", "линзы", "спортзал", "фитнес", "тонометр")),
    ("🎬", "Развлечения", ("кино", "театр", "игры", "подписка", "концерт",
                           "развлечения", "музей", "книга", "steam")),
    ("🎓", "Образование", ("курсы", "обучение", "учеба", "учёба", "репетитор",
                           "школа", "университет")),
    ("🎁", "Подарки", ("подарок", "подарки", "цветы", "днюха")),
    ("🧸", "Дети", ("сыну", "сына", "дочке", "дочери", "ребенку", "ребёнку",
                    "детская", "детский", "детское", "детские", "боди", "слипа",
                    "слипы", "подгузники", "коляска", "игрушки", "погремушка",
                    "пеленки", "пелёнки", "смесь")),
    ("🤝", "Долги", ("долг", "долги", "займ", "кредит", "рассрочка", "отдал долг")),
    ("🏦", "Накопления", ("накопления", "накопить", "копилка", "отложил",
                          "сбережения", "подушка")),
    ("📦", "Прочее", ()),
)

#: Доходные категории. «Прочее» и здесь запасное — на него падают доходы,
#: для которых не нашлось ключевого слова.
DEFAULT_INCOME_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("💼", "Зарплата", ("зарплата", "зп", "оклад", "аванс", "получка")),
    ("🏅", "Премия", ("премия", "бонус", "квартальная", "13-я", "тринадцатая")),
    ("🛠", "Подработка", ("подработка", "халтура", "фриланс", "заказ", "шабашка")),
    ("↩️", "Возвраты", ("возврат", "вернули", "кэшбэк", "кешбэк", "кешбек",
                        "компенсация", "налоговый", "вычет")),
    ("📈", "Проценты", ("проценты", "вклад", "дивиденды", "купон", "накопительный")),
    ("🎁", "Подарили", ("подарили", "подарок", "дарение")),
    ("🤝", "Долги", ("долг", "займ", "занял", "одолжил", "в долг")),
    ("🏦", "Накопления", ("со счёта", "со счета", "снял накопления", "из копилки")),
    ("💰", "Прочее", ()),
)


@dataclass(frozen=True)
class Category:
    id: int
    kind: str
    name: str
    emoji: str
    keywords: tuple[str, ...] = field(default=())
    is_fallback: bool = False
    #: Долги и накопления: движение денег, но не трата и не заработок.
    is_transfer: bool = False

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.name}"


@dataclass(frozen=True)
class Transaction:
    id: int
    kind: str
    amount: int
    note: str
    happened_on: dt.date
    category_id: Optional[int]
    category_name: str
    category_emoji: str

    @property
    def is_income(self) -> bool:
        return self.kind == INCOME

    @property
    def signed(self) -> int:
        return self.amount if self.is_income else -self.amount

    @property
    def category_title(self) -> str:
        return f"{self.category_emoji} {self.category_name}"


@dataclass(frozen=True)
class CategoryTotal:
    category_id: Optional[int]
    name: str
    emoji: str
    total: int
    count: int

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.name}"


def _split_keywords(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def join_keywords(keywords: Sequence[str]) -> str:
    seen: list[str] = []
    for keyword in keywords:
        keyword = keyword.strip().lower()
        if keyword and keyword not in seen:
            seen.append(keyword)
    return ",".join(seen)


def _row_to_category(row: aiosqlite.Row) -> Category:
    return Category(
        id=row["id"],
        kind=row["kind"],
        name=row["name"],
        emoji=row["emoji"],
        keywords=_split_keywords(row["keywords"]),
        is_fallback=bool(row["is_fallback"]),
        is_transfer=bool(row["is_transfer"]),
    )


def _row_to_transaction(row: aiosqlite.Row) -> Transaction:
    return Transaction(
        id=row["id"],
        kind=row["kind"],
        amount=row["amount"],
        note=row["note"],
        happened_on=dt.date.fromisoformat(row["happened_on"]),
        category_id=row["category_id"],
        category_name=row["name"] or "Без категории",
        category_emoji=row["emoji"] or "❔",
    )


_CATEGORY_COLUMNS = "id, kind, name, emoji, keywords, is_fallback, is_transfer"


def _transfer_clause(transfers: Optional[bool]) -> str:
    """Условие «только перекладывания» / «всё, кроме них» / «всё подряд»."""
    if transfers is None:
        return ""
    return f" AND COALESCE(c.is_transfer, 0) = {1 if transfers else 0}"

_TRANSACTION_SELECT = """
SELECT t.id, t.kind, t.amount, t.note, t.happened_on, t.category_id, c.name, c.emoji
FROM transactions t
LEFT JOIN money_categories c ON c.id = t.category_id
"""


class MoneyRepo:
    """Примесь к Database: запросы раздела «Деньги»."""

    conn: aiosqlite.Connection

    async def seed_money_categories(self, user_id: int) -> None:
        """Создаёт набор категорий по умолчанию (идемпотентно).

        Зовётся и при первом знакомстве, и при запуске: категории, добавленные
        в новой версии бота, должны появиться и у того, кто завёл дневник
        раньше. Свои категории и переименованные чужие не трогаются — за это
        отвечает INSERT OR IGNORE.
        """
        rows = []
        for kind, defaults in (
            (EXPENSE, DEFAULT_EXPENSE_CATEGORIES),
            (INCOME, DEFAULT_INCOME_CATEGORIES),
        ):
            rows.extend(
                (
                    user_id,
                    kind,
                    name,
                    emoji,
                    join_keywords(keywords),
                    int(not keywords),
                    int(name in TRANSFER_CATEGORIES),
                )
                for emoji, name, keywords in defaults
            )
        await self.conn.executemany(
            "INSERT OR IGNORE INTO money_categories"
            " (user_id, kind, name, emoji, keywords, is_fallback, is_transfer)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self.conn.commit()

    async def sync_money_categories(self) -> int:
        """Доливает новые категории всем, кто завёл дневник раньше.

        Категория, появившаяся в новой версии, иначе не досталась бы старому
        дневнику: он получает свой набор один раз, при первом знакомстве.
        """
        cur = await self.conn.execute("SELECT user_id FROM users")
        users = [row["user_id"] for row in await cur.fetchall()]
        for user_id in users:
            await self.seed_money_categories(user_id)
        return len(users)

    # ------------------------------------------------------------ категории

    async def list_categories(self, user_id: int, kind: str = EXPENSE) -> list[Category]:
        cur = await self.conn.execute(
            f"SELECT {_CATEGORY_COLUMNS} FROM money_categories"
            " WHERE user_id = ? AND kind = ? ORDER BY is_fallback, name",
            (user_id, kind),
        )
        return [_row_to_category(row) for row in await cur.fetchall()]

    async def get_category(self, user_id: int, category_id: int) -> Optional[Category]:
        cur = await self.conn.execute(
            f"SELECT {_CATEGORY_COLUMNS} FROM money_categories"
            " WHERE user_id = ? AND id = ?",
            (user_id, category_id),
        )
        row = await cur.fetchone()
        return _row_to_category(row) if row else None

    async def find_category_by_name(
        self, user_id: int, name: str, kind: Optional[str] = None
    ) -> Optional[Category]:
        query = (
            f"SELECT {_CATEGORY_COLUMNS} FROM money_categories"
            " WHERE user_id = ? AND lower(name) = lower(?)"
        )
        params: list = [user_id, name.strip()]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        cur = await self.conn.execute(query, params)
        row = await cur.fetchone()
        return _row_to_category(row) if row else None

    async def get_fallback_category(self, user_id: int, kind: str = EXPENSE) -> Optional[Category]:
        cur = await self.conn.execute(
            f"SELECT {_CATEGORY_COLUMNS} FROM money_categories"
            " WHERE user_id = ? AND kind = ? ORDER BY is_fallback DESC, id LIMIT 1",
            (user_id, kind),
        )
        row = await cur.fetchone()
        return _row_to_category(row) if row else None

    async def add_category(
        self,
        user_id: int,
        name: str,
        emoji: str,
        keywords: Sequence[str] = (),
        kind: str = EXPENSE,
    ) -> Optional[Category]:
        try:
            cur = await self.conn.execute(
                "INSERT INTO money_categories (user_id, kind, name, emoji, keywords)"
                " VALUES (?, ?, ?, ?, ?)",
                (user_id, kind, name.strip(), emoji, join_keywords(keywords)),
            )
        except aiosqlite.IntegrityError:
            return None
        await self.conn.commit()
        return await self.get_category(user_id, cur.lastrowid)

    async def delete_category(self, user_id: int, category_id: int) -> bool:
        """Удаляет категорию, перенося её операции в запасную."""
        category = await self.get_category(user_id, category_id)
        if category is None or category.is_fallback:
            return False
        fallback = await self.get_fallback_category(user_id, category.kind)
        fallback_id = fallback.id if fallback and fallback.id != category_id else None
        await self.conn.execute(
            "UPDATE transactions SET category_id = ? WHERE user_id = ? AND category_id = ?",
            (fallback_id, user_id, category_id),
        )
        await self.conn.execute(
            "DELETE FROM money_limits WHERE user_id = ? AND category_id = ?",
            (user_id, category_id),
        )
        await self.conn.execute(
            "DELETE FROM money_categories WHERE user_id = ? AND id = ?", (user_id, category_id)
        )
        await self.conn.commit()
        return True

    async def add_keyword(self, user_id: int, category_id: int, keyword: str) -> None:
        """Привязывает слово к категории и снимает его с остальных того же вида."""
        keyword = keyword.strip().lower()
        if not keyword:
            return
        target = await self.get_category(user_id, category_id)
        if target is None:
            return

        for category in await self.list_categories(user_id, target.kind):
            keywords = list(category.keywords)
            if category.id == category_id:
                if keyword in keywords:
                    continue
                keywords.append(keyword)
            elif keyword in keywords:
                keywords.remove(keyword)
            else:
                continue
            await self.conn.execute(
                "UPDATE money_categories SET keywords = ? WHERE user_id = ? AND id = ?",
                (join_keywords(keywords), user_id, category.id),
            )
        await self.conn.commit()

    # ------------------------------------------------------------- операции

    async def add_transaction(
        self,
        user_id: int,
        kind: str,
        amount: int,
        note: str,
        happened_on: dt.date,
        category_id: Optional[int],
    ) -> Transaction:
        cur = await self.conn.execute(
            "INSERT INTO transactions (user_id, kind, amount, note, happened_on, category_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, kind, amount, note.strip(), happened_on.isoformat(), category_id),
        )
        await self.conn.commit()
        created = await self.get_transaction(user_id, cur.lastrowid)
        assert created is not None
        return created

    async def has_transaction_on(self, user_id: int, on_date: dt.date) -> bool:
        """Записывал ли пользователь что-нибудь за этот день."""
        cur = await self.conn.execute(
            "SELECT 1 FROM transactions WHERE user_id = ? AND happened_on = ? LIMIT 1",
            (user_id, on_date.isoformat()),
        )
        return await cur.fetchone() is not None

    async def get_transaction(self, user_id: int, transaction_id: int) -> Optional[Transaction]:
        cur = await self.conn.execute(
            _TRANSACTION_SELECT + " WHERE t.user_id = ? AND t.id = ?", (user_id, transaction_id)
        )
        row = await cur.fetchone()
        return _row_to_transaction(row) if row else None

    async def last_transactions(self, user_id: int, limit: int = 10) -> list[Transaction]:
        cur = await self.conn.execute(
            _TRANSACTION_SELECT + " WHERE t.user_id = ? ORDER BY t.id DESC LIMIT ?",
            (user_id, limit),
        )
        return [_row_to_transaction(row) for row in await cur.fetchall()]

    async def delete_transaction(self, user_id: int, transaction_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM transactions WHERE user_id = ? AND id = ?", (user_id, transaction_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def set_transaction_category(
        self, user_id: int, transaction_id: int, category_id: int
    ) -> Optional[Transaction]:
        cur = await self.conn.execute(
            "UPDATE transactions SET category_id = ? WHERE user_id = ? AND id = ?",
            (category_id, user_id, transaction_id),
        )
        await self.conn.commit()
        if cur.rowcount == 0:
            return None
        return await self.get_transaction(user_id, transaction_id)

    async def transactions_between(
        self, user_id: int, start: dt.date, end: dt.date, kind: Optional[str] = None
    ) -> list[Transaction]:
        query = _TRANSACTION_SELECT + " WHERE t.user_id = ? AND t.happened_on BETWEEN ? AND ?"
        params: list = [user_id, start.isoformat(), end.isoformat()]
        if kind is not None:
            query += " AND t.kind = ?"
            params.append(kind)
        cur = await self.conn.execute(query + " ORDER BY t.happened_on, t.id", params)
        return [_row_to_transaction(row) for row in await cur.fetchall()]

    async def count_transactions(self, user_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS cnt FROM transactions WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return row["cnt"] if row else 0

    async def first_transaction_date(self, user_id: int) -> Optional[dt.date]:
        cur = await self.conn.execute(
            "SELECT MIN(happened_on) AS first FROM transactions WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return dt.date.fromisoformat(row["first"]) if row and row["first"] else None

    # --------------------------------------------------------------- итоги

    async def totals_by_category(
        self,
        user_id: int,
        start: dt.date,
        end: dt.date,
        kind: str = EXPENSE,
        transfers: Optional[bool] = None,
    ) -> list[CategoryTotal]:
        cur = await self.conn.execute(
            f"""
            SELECT t.category_id AS category_id,
                   COALESCE(c.name, 'Без категории') AS name,
                   COALESCE(c.emoji, '❔') AS emoji,
                   SUM(t.amount) AS total,
                   COUNT(*) AS cnt
            FROM transactions t
            LEFT JOIN money_categories c ON c.id = t.category_id
            WHERE t.user_id = ? AND t.kind = ? AND t.happened_on BETWEEN ? AND ?
            {_transfer_clause(transfers)}
            GROUP BY t.category_id
            ORDER BY total DESC
            """,
            (user_id, kind, start.isoformat(), end.isoformat()),
        )
        return [
            CategoryTotal(
                category_id=row["category_id"],
                name=row["name"],
                emoji=row["emoji"],
                total=row["total"],
                count=row["cnt"],
            )
            for row in await cur.fetchall()
        ]

    async def total_between(
        self,
        user_id: int,
        start: dt.date,
        end: dt.date,
        kind: str = EXPENSE,
        transfers: Optional[bool] = None,
    ) -> tuple[int, int]:
        """Сумма и число записей. `transfers`: None — все, False — без долгов
        и накоплений, True — только они."""
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(t.amount), 0) AS total, COUNT(*) AS cnt"
            " FROM transactions t"
            " LEFT JOIN money_categories c ON c.id = t.category_id"
            " WHERE t.user_id = ? AND t.kind = ? AND t.happened_on BETWEEN ? AND ?"
            + _transfer_clause(transfers),
            (user_id, kind, start.isoformat(), end.isoformat()),
        )
        row = await cur.fetchone()
        return row["total"], row["cnt"]

    async def category_total_between(
        self, user_id: int, category_id: int, start: dt.date, end: dt.date
    ) -> int:
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions"
            " WHERE user_id = ? AND category_id = ? AND happened_on BETWEEN ? AND ?",
            (user_id, category_id, start.isoformat(), end.isoformat()),
        )
        row = await cur.fetchone()
        return row["total"]

    # -------------------------------------------------------------- лимиты

    async def set_limit(self, user_id: int, category_id: int, amount: int) -> None:
        await self.conn.execute(
            "INSERT INTO money_limits (user_id, category_id, amount) VALUES (?, ?, ?)"
            " ON CONFLICT(user_id, category_id) DO UPDATE SET amount = excluded.amount",
            (user_id, category_id, amount),
        )
        await self.conn.commit()

    async def delete_limit(self, user_id: int, category_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM money_limits WHERE user_id = ? AND category_id = ?",
            (user_id, category_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_limit(self, user_id: int, category_id: int) -> Optional[int]:
        cur = await self.conn.execute(
            "SELECT amount FROM money_limits WHERE user_id = ? AND category_id = ?",
            (user_id, category_id),
        )
        row = await cur.fetchone()
        return row["amount"] if row else None

    async def list_limits(self, user_id: int) -> list[tuple[int, int]]:
        """Возвращает [(category_id, amount)], где 0 — общий лимит."""
        cur = await self.conn.execute(
            "SELECT category_id, amount FROM money_limits WHERE user_id = ? ORDER BY category_id",
            (user_id,),
        )
        return [(row["category_id"], row["amount"]) for row in await cur.fetchall()]
