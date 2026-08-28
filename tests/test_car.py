"""Пробег: разбор строки, что из него считается и как это записывается."""

from __future__ import annotations

import datetime as dt
import unittest

from bot import sections
from bot.car.db import Reading
from bot.car.parsing import parse_mileage, strip_mileage
from bot.car.stats import ride_between
from bot.db import UserSettings

from .support import memory_db
from .test_handlers import USER_ID, BotTestCase

TODAY = dt.date(2026, 8, 28)
USER = UserSettings(user_id=777, tz="Europe/Moscow", currency="₽")


class ParseTest(unittest.TestCase):
    def test_understood_forms(self):
        for text in ("пробег 203116", "203116 км", "одометр 203 116", "пробег: 203116"):
            with self.subTest(text=text):
                self.assertEqual(parse_mileage(text), 203116)

    def test_needs_a_word_next_to_the_number(self):
        """Голое число — это скорее сумма траты, чем показание одометра."""
        self.assertIsNone(parse_mileage("203116"))
        self.assertIsNone(parse_mileage("кофе 300"))

    def test_nonsense_values(self):
        self.assertIsNone(parse_mileage("пробег 5"))
        self.assertIsNone(parse_mileage("пробег 9999999999"))

    def test_stripping_leaves_the_rest(self):
        """«бензин 1999 пробег 203116» — это и трата, и показание."""
        self.assertEqual(strip_mileage("бензин 1999 пробег 203116"), "бензин 1999")
        self.assertEqual(strip_mileage("кофе 300"), "кофе 300")


class RideTest(unittest.TestCase):
    def ride(self, *pairs, before=None):
        readings = [Reading(dt.date(2026, 8, day), km) for day, km in pairs]
        return ride_between(readings, before)

    def test_distance_and_average(self):
        ride = self.ride((21, 203000), (28, 203700))
        self.assertEqual(ride.driven, 700)
        self.assertEqual(ride.days, 7)
        self.assertEqual(ride.per_day, 100)

    def test_reading_before_the_period_is_the_start(self):
        ride = self.ride((28, 203700), before=Reading(dt.date(2026, 8, 27), 203600))
        self.assertEqual(ride.driven, 100)

    def test_single_reading_says_nothing(self):
        self.assertIsNone(self.ride((28, 203700)))
        self.assertIsNone(self.ride())


class StorageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = memory_db()
        await self.db.connect()
        await self.db.ensure_user(USER.user_id)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_one_reading_per_day(self):
        await self.db.set_reading(USER.user_id, TODAY, 203116)
        await self.db.set_reading(USER.user_id, TODAY, 203120)  # поправился

        self.assertEqual(await self.db.count_readings(USER.user_id), 1)
        self.assertEqual((await self.db.last_reading(USER.user_id)).km, 203120)

    async def test_report_counts_the_week(self):
        from bot.car.stats import build_report

        await self.db.set_reading(USER.user_id, TODAY - dt.timedelta(days=7), 203000)
        await self.db.set_reading(USER.user_id, TODAY, 203700)

        text = await build_report(self.db, USER, TODAY)
        self.assertIn("203 700 км", text)
        self.assertIn("За неделю", text)

    async def test_price_per_kilometer(self):
        from bot.car.stats import build_report
        from bot.money.db import EXPENSE

        await self.db.set_reading(USER.user_id, TODAY.replace(day=1), 203000)
        await self.db.set_reading(USER.user_id, TODAY, 204000)
        category = await self.db.find_category_by_name(USER.user_id, "Транспорт", EXPENSE)
        await self.db.add_transaction(
            USER.user_id, EXPENSE, 500000, "бензин", TODAY, category.id
        )

        text = await build_report(self.db, USER, TODAY)
        self.assertIn("за км", text)
        self.assertIn("5 ₽", text.replace(" ", " "))  # 5000 ₽ на 1000 км

    async def test_empty_report_explains_itself(self):
        from bot.car.stats import build_report

        self.assertIn("Показаний ещё нет", await build_report(self.db, USER, TODAY))


class CarFlowTest(BotTestCase):
    async def test_free_text_records_mileage(self):
        answer = await self.send("пробег 203116")
        self.assertIn("203 116", answer)
        self.assertEqual((await self.db.last_reading(USER_ID)).km, 203116)

    async def test_fuel_line_is_both_expense_and_reading(self):
        """«бензин 1999 пробег 203116» — одна строка, две записи."""
        await self.send("бензин 1999 пробег 203116")

        self.assertEqual((await self.db.last_reading(USER_ID)).km, 203116)
        transaction = (await self.db.last_transactions(USER_ID))[0]
        self.assertEqual(transaction.amount, 199900)

    async def test_distance_since_yesterday(self):
        from bot.middlewares import now_for

        today = now_for("Europe/Moscow").date()
        await self.db.set_reading(USER_ID, today - dt.timedelta(days=1), 203000)

        answer = await self.send("пробег 203100")
        self.assertIn("Проехал со вчера", answer)
        self.assertIn("100 км", answer)

    async def test_lower_reading_is_questioned(self):
        from bot.middlewares import now_for

        today = now_for("Europe/Moscow").date()
        await self.db.set_reading(USER_ID, today - dt.timedelta(days=1), 203000)
        self.assertIn("больше", await self.send("пробег 202000"))

    async def test_did_not_drive_button(self):
        from bot.middlewares import now_for

        today = now_for("Europe/Moscow").date()
        await self.db.set_reading(USER_ID, today - dt.timedelta(days=1), 203000)

        await self.click("car:same")
        self.assertEqual((await self.db.reading_on(USER_ID, today)).km, 203000)

    async def test_button_asks_and_saves(self):
        await self.click("do:car:add")
        self.assertIn("одометра", self.bot.texts[-1])

        await self.send("203116")
        self.assertEqual((await self.db.last_reading(USER_ID)).km, 203116)

    async def test_command(self):
        self.assertIn("Пробег", await self.send("/car"))

    async def test_reminder_is_seeded(self):
        await self.db.ensure_user(USER_ID)  # напоминания ставятся при знакомстве
        topics = {item.topic for item in await self.db.list_reminders(USER_ID)}
        self.assertIn(sections.CAR, topics)


if __name__ == "__main__":
    unittest.main()
