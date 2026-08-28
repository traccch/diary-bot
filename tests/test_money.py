"""Раздел «Деньги»: разбор строк, доходы, категории, лимиты, баланс."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from bot.db import Database, UserSettings
from bot.money.db import EXPENSE, INCOME, TOTAL_LIMIT_CATEGORY
from bot.money.parsing import ParseError, match_category, parse_amount, parse_transaction
from bot.money.stats import balance_text, build_report, check_limits, period_range

TODAY = dt.date(2026, 8, 17)
USER = UserSettings(user_id=777, tz="Europe/Moscow", currency="₽")


class ParseTest(unittest.TestCase):
    def parse(self, text: str):
        return parse_transaction(text, TODAY)

    def test_simple_expense(self):
        parsed = self.parse("кофе 300")
        self.assertEqual(parsed.kind, EXPENSE)
        self.assertEqual(parsed.amount, 30000)
        self.assertEqual(parsed.note, "кофе")
        self.assertEqual(parsed.happened_on, TODAY)

    def test_word_order_and_formats(self):
        cases = {
            "450 такси": 45000,
            "продукты 1 250,50": 125050,
            "аренда 45к": 4_500_000,
            "кофе 300р": 30000,
            "₽300 кофе": 30000,
        }
        for text, amount in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.parse(text).amount, amount)

    def test_income_needs_a_plus(self):
        income = self.parse("+90000 зарплата")
        self.assertEqual(income.kind, INCOME)
        self.assertEqual(income.amount, 9_000_000)
        self.assertEqual(income.note, "зарплата")

        # без знака это расход, даже если слово похоже на доход
        self.assertEqual(self.parse("зарплата 90000").kind, EXPENSE)

    def test_explicit_minus_is_expense(self):
        self.assertEqual(self.parse("-500 книга").kind, EXPENSE)

    def test_dates(self):
        self.assertEqual(self.parse("кино 800 вчера").happened_on, TODAY - dt.timedelta(days=1))
        self.assertEqual(self.parse("подарок 3000 05.08").happened_on, dt.date(2026, 8, 5))

    def test_no_amount(self):
        self.assertIsNone(self.parse("просто текст"))

    def test_zero_and_absurd(self):
        with self.assertRaises(ParseError):
            self.parse("кофе 0")
        with self.assertRaises(ParseError):
            self.parse("кофе 99999999999999")

    def test_parse_amount_helper(self):
        self.assertEqual(parse_amount("Кафе 8000"), (800000, "Кафе"))
        self.assertIsNone(parse_amount("Кафе"))


class MoneyStorageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "t.db"), "Europe/Moscow")
        await self.db.connect()
        self.user = await self.db.ensure_user(USER.user_id)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def category(self, name: str, kind: str = EXPENSE):
        found = await self.db.find_category_by_name(USER.user_id, name, kind)
        self.assertIsNotNone(found, f"нет категории {name}")
        return found

    async def add(self, kind, amount, note, category, on=TODAY):
        return await self.db.add_transaction(
            USER.user_id, kind, amount, note, on, category.id if category else None
        )

    async def test_default_categories_for_both_kinds(self):
        expenses = await self.db.list_categories(USER.user_id, EXPENSE)
        incomes = await self.db.list_categories(USER.user_id, INCOME)
        self.assertGreater(len(expenses), 5)
        self.assertGreater(len(incomes), 3)
        self.assertTrue(any(c.name == "Зарплата" for c in incomes))
        self.assertTrue(any(c.name == "Продукты" for c in expenses))

    async def test_same_name_in_both_kinds(self):
        """«Подарки» есть и в расходах, и в доходах — они не мешают друг другу."""
        expense = await self.category("Подарки", EXPENSE)
        income = await self.category("Подарили", INCOME)
        self.assertNotEqual(expense.id, income.id)

    async def test_seeding_is_idempotent(self):
        before = len(await self.db.list_categories(USER.user_id, EXPENSE))
        await self.db.seed_money_categories(USER.user_id)
        self.assertEqual(len(await self.db.list_categories(USER.user_id, EXPENSE)), before)

    async def test_category_matching(self):
        categories = await self.db.list_categories(USER.user_id, EXPENSE)
        self.assertEqual(match_category("кофе", categories).name, "Кафе")
        self.assertEqual(match_category("такси до дома", categories).name, "Транспорт")
        self.assertIsNone(match_category("шаурма", categories))

    async def test_income_categories_are_matched_separately(self):
        incomes = await self.db.list_categories(USER.user_id, INCOME)
        self.assertEqual(match_category("зарплата", incomes).name, "Зарплата")
        self.assertEqual(match_category("кэшбэк", incomes).name, "Возвраты")

    async def test_totals_and_balance(self):
        cafe = await self.category("Кафе")
        salary = await self.category("Зарплата", INCOME)
        await self.add(EXPENSE, 30000, "кофе", cafe)
        await self.add(EXPENSE, 20000, "обед", cafe)
        await self.add(INCOME, 9_000_000, "зарплата", salary)

        spent, count = await self.db.total_between(USER.user_id, TODAY, TODAY, EXPENSE)
        self.assertEqual((spent, count), (50000, 2))
        earned, _ = await self.db.total_between(USER.user_id, TODAY, TODAY, INCOME)
        self.assertEqual(earned, 9_000_000)

        text = await balance_text(self.db, self.user, TODAY)
        self.assertIn("Пришло", text)
        self.assertIn("Остаток", text)

    async def test_deleting_category_moves_transactions(self):
        pizza = await self.db.add_category(USER.user_id, "Пицца", "🍕", ["додо"])
        created = await self.add(EXPENSE, 70000, "додо", pizza)
        self.assertTrue(await self.db.delete_category(USER.user_id, pizza.id))
        moved = await self.db.get_transaction(USER.user_id, created.id)
        self.assertEqual(moved.category_name, "Прочее")

    async def test_fallback_cannot_be_deleted(self):
        fallback = await self.db.get_fallback_category(USER.user_id, EXPENSE)
        self.assertEqual(fallback.name, "Прочее")
        self.assertFalse(await self.db.delete_category(USER.user_id, fallback.id))

    async def test_keyword_moves_between_categories(self):
        cafe = await self.category("Кафе")
        groceries = await self.category("Продукты")
        await self.db.add_keyword(USER.user_id, cafe.id, "шаурма")
        self.assertIn("шаурма", (await self.category("Кафе")).keywords)

        await self.db.add_keyword(USER.user_id, groceries.id, "шаурма")
        self.assertNotIn("шаурма", (await self.category("Кафе")).keywords)
        self.assertIn("шаурма", (await self.category("Продукты")).keywords)

    async def test_limits(self):
        await self.db.set_limit(USER.user_id, TOTAL_LIMIT_CATEGORY, 100000)
        cafe = await self.category("Кафе")
        await self.add(EXPENSE, 90000, "кофе", cafe)

        warnings = await check_limits(self.db, self.user, cafe.id, TODAY)
        self.assertTrue(any("🟡" in w for w in warnings))

        await self.add(EXPENSE, 30000, "ещё кофе", cafe)
        warnings = await check_limits(self.db, self.user, cafe.id, TODAY)
        self.assertTrue(any("🔴" in w for w in warnings))

        self.assertTrue(await self.db.delete_limit(USER.user_id, TOTAL_LIMIT_CATEGORY))
        self.assertEqual(await check_limits(self.db, self.user, cafe.id, TODAY), [])

    async def test_income_does_not_touch_limits(self):
        await self.db.set_limit(USER.user_id, TOTAL_LIMIT_CATEGORY, 100000)
        salary = await self.category("Зарплата", INCOME)
        await self.add(INCOME, 9_000_000, "зарплата", salary)
        self.assertEqual(await check_limits(self.db, self.user, salary.id, TODAY), [])

    async def test_report(self):
        self.assertIn("пусто", await build_report(self.db, self.user, "month", TODAY))

        cafe = await self.category("Кафе")
        salary = await self.category("Зарплата", INCOME)
        await self.add(EXPENSE, 30000, "кофе", cafe)
        await self.add(INCOME, 9_000_000, "зарплата", salary)

        text = await build_report(self.db, self.user, "month", TODAY)
        self.assertIn("Доходы", text)
        self.assertIn("Расходы", text)
        self.assertIn("Куда ушло", text)
        self.assertIn("Остаток", text)

    async def test_periods(self):
        self.assertEqual(period_range("day", TODAY)[0], TODAY)
        self.assertEqual(period_range("month", TODAY)[0], dt.date(2026, 8, 1))
        self.assertEqual(period_range("week", TODAY)[0], dt.date(2026, 8, 17))
        self.assertEqual(period_range("all", TODAY)[0].year, 1970)

    async def test_data_is_isolated_between_users(self):
        cafe = await self.category("Кафе")
        created = await self.add(EXPENSE, 30000, "кофе", cafe)
        await self.db.ensure_user(USER.user_id + 1)
        self.assertIsNone(await self.db.get_transaction(USER.user_id + 1, created.id))


if __name__ == "__main__":
    unittest.main()


class TransferCategoriesTest(unittest.IsolatedAsyncioTestCase):
    """Долги и накопления: деньги двигаются, но это не трата и не заработок."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "t.db"), "Europe/Moscow")
        await self.db.connect()
        self.user = await self.db.ensure_user(USER.user_id)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def spend(self, amount: int, note: str, name: str, kind: str = EXPENSE):
        category = await self.db.find_category_by_name(USER.user_id, name, kind)
        self.assertIsNotNone(category, f"нет категории {name}")
        await self.db.add_transaction(
            USER.user_id, kind, amount, note, TODAY, category.id
        )

    async def test_categories_exist_and_are_marked(self):
        expenses = {c.name: c for c in await self.db.list_categories(USER.user_id, EXPENSE)}
        self.assertTrue(expenses["Долги"].is_transfer)
        self.assertTrue(expenses["Накопления"].is_transfer)
        self.assertFalse(expenses["Продукты"].is_transfer)

        incomes = {c.name: c for c in await self.db.list_categories(USER.user_id, INCOME)}
        self.assertTrue(incomes["Долги"].is_transfer)

    async def test_totals_can_exclude_them(self):
        await self.spend(30000, "кофе", "Кафе")
        await self.spend(500000, "отложил", "Накопления")

        everything, _ = await self.db.total_between(USER.user_id, TODAY, TODAY, EXPENSE)
        real, count = await self.db.total_between(
            USER.user_id, TODAY, TODAY, EXPENSE, transfers=False
        )
        moved, _ = await self.db.total_between(
            USER.user_id, TODAY, TODAY, EXPENSE, transfers=True
        )
        self.assertEqual(everything, 530000)
        self.assertEqual((real, count), (30000, 1))
        self.assertEqual(moved, 500000)

    async def test_report_keeps_them_out_of_spending(self):
        await self.spend(30000, "кофе", "Кафе")
        await self.spend(500000, "отложил", "Накопления")
        await self.spend(100000, "займ у мамы", "Долги", INCOME)

        text = await build_report(self.db, self.user, "month", TODAY)
        self.assertIn("Расходы: <b>300", text)  # 300 ₽, а не 5 300
        self.assertIn("Долги и накопления", text)
        self.assertNotIn("🏦 Накопления", text)  # в разбивке трат их нет

    async def test_balance_says_it_out_loud(self):
        await self.spend(500000, "отложил", "Накопления")
        text = await balance_text(self.db, self.user, TODAY)
        self.assertIn("переложены", text)

    async def test_limits_ignore_them(self):
        await self.db.set_limit(USER.user_id, TOTAL_LIMIT_CATEGORY, 1_000_00)
        await self.spend(500000, "отложил", "Накопления")
        # 5 000 ₽ отложено при лимите 1 000 ₽ — это не перерасход
        self.assertEqual(await check_limits(self.db, self.user, None, TODAY), [])

    async def test_old_diary_gets_new_categories(self):
        """Категория, появившаяся в новой версии, должна доехать до старой базы."""
        await self.db.conn.execute(
            "DELETE FROM money_categories WHERE user_id = ? AND name IN ('Долги', 'Дети')",
            (USER.user_id,),
        )
        await self.db.conn.commit()

        await self.db.sync_money_categories()
        names = {c.name for c in await self.db.list_categories(USER.user_id, EXPENSE)}
        self.assertIn("Долги", names)
        self.assertIn("Дети", names)

    async def test_kids_category_catches_the_usual_words(self):
        categories = await self.db.list_categories(USER.user_id, EXPENSE)
        for note in ("три слипы сыну на вырост", "пять боди Льву", "детская корзина"):
            with self.subTest(note=note):
                self.assertEqual(match_category(note, categories).name, "Дети")

        # аптека важнее: слово длиннее и точнее
        self.assertEqual(
            match_category("аптека, перекись сыну", categories).name, "Здоровье"
        )
