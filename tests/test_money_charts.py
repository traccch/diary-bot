"""График трат: подготовка данных, выборка по дням и сама картинка."""

from __future__ import annotations

import datetime as dt
import unittest

from bot.db import UserSettings
from bot.money import charts
from bot.money.db import EXPENSE, INCOME, CategoryTotal

from .support import memory_db

TODAY = dt.date(2026, 11, 21)
USER = UserSettings(user_id=777, tz="Europe/Moscow", currency="₽")


def total(name: str, amount: int) -> CategoryTotal:
    return CategoryTotal(category_id=None, name=name, emoji="", total=amount, count=1)


class PrepareTest(unittest.TestCase):
    def test_fill_days_puts_zeros_into_gaps(self):
        start = dt.date(2026, 11, 1)
        filled = charts.fill_days([(start, 100), (start + dt.timedelta(2), 300)],
                                  start, start + dt.timedelta(3))
        self.assertEqual([amount for _, amount in filled], [100, 0, 300, 0])

    def test_by_month(self):
        months = charts.by_month([
            (dt.date(2026, 10, 30), 100), (dt.date(2026, 10, 31), 50),
            (dt.date(2026, 11, 2), 70),
        ])
        self.assertEqual(months, [(dt.date(2026, 10, 1), 150), (dt.date(2026, 11, 1), 70)])

    def test_long_tail_folds_into_rest(self):
        totals = [total(f"к{i}", 1000 - i) for i in range(10)]
        bars = charts.top_categories(totals, limit=3)
        self.assertEqual([name for name, _ in bars], ["к0", "к1", "к2", "Остальное"])
        self.assertEqual(sum(amount for _, amount in bars), sum(t.total for t in totals))

    def test_no_rest_when_few_categories(self):
        self.assertEqual(len(charts.top_categories([total("а", 1)], limit=3)), 1)

    def test_unfinished_month(self):
        self.assertTrue(charts._month_unfinished(dt.date(2026, 11, 21)))
        self.assertFalse(charts._month_unfinished(dt.date(2026, 11, 30)))
        self.assertFalse(charts._month_unfinished(dt.date(2026, 12, 31)))


@unittest.skipUnless(charts.available(), "matplotlib не установлен")
class PictureTest(unittest.TestCase):
    cats = [total("Продукты", 1_250_000), total("Кафе", 480_000)]

    def test_month(self):
        start = TODAY.replace(day=1)
        daily = [(start + dt.timedelta(days), 30_000) for days in range(0, 20, 3)]
        image = charts.money_png(daily, self.cats, start, TODAY, "Расходы за ноябрь")
        self.assertTrue(image.startswith(b"\x89PNG"))

    def test_long_period_goes_monthly(self):
        start = dt.date(2025, 1, 1)
        daily = [(start + dt.timedelta(days), 10_000) for days in range(0, 600, 5)]
        self.assertTrue(charts.money_png(daily, self.cats, start, TODAY, "Всё").startswith(b"\x89PNG"))

    def test_single_day_has_only_categories(self):
        image = charts.money_png([(TODAY, 90_000)], self.cats, TODAY, TODAY, "Сегодня")
        self.assertTrue(image.startswith(b"\x89PNG"))

    def test_nothing_spent_is_refused(self):
        with self.assertRaises(ValueError):
            charts.money_png([], [], TODAY, TODAY, "Пусто")


class DailyTotalsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = memory_db()
        await self.db.connect()
        await self.db.ensure_user(USER.user_id)

    async def asyncTearDown(self):
        await self.db.close()

    async def add(self, kind, amount, name, day):
        category = await self.db.find_category_by_name(USER.user_id, name, kind)
        await self.db.add_transaction(USER.user_id, kind, amount, "", day, category.id)

    async def test_sums_per_day_without_transfers_and_income(self):
        await self.add(EXPENSE, 30_000, "Кафе", TODAY)
        await self.add(EXPENSE, 20_000, "Продукты", TODAY)
        await self.add(EXPENSE, 5_000, "Кафе", TODAY - dt.timedelta(days=1))
        await self.add(EXPENSE, 1_000_000, "Накопления", TODAY)  # перекладывание
        await self.add(INCOME, 9_000_000, "Зарплата", TODAY)

        daily = await self.db.daily_totals(
            USER.user_id, TODAY - dt.timedelta(days=7), TODAY, EXPENSE, transfers=False
        )
        self.assertEqual(daily, [(TODAY - dt.timedelta(days=1), 5_000), (TODAY, 50_000)])


if __name__ == "__main__":
    unittest.main()
