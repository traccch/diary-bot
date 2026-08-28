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
    def said(self) -> str:
        """Всё сказанное за ход: за записью пробега может идти вопрос про ТО."""
        return "\n".join(self.bot.texts)

    async def test_free_text_records_mileage(self):
        await self.send("пробег 203116")
        self.assertIn("203 116", self.said())
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

        await self.send("пробег 203100")
        self.assertIn("Проехал со вчера", self.said())
        self.assertIn("100 км", self.said())

    async def test_lower_reading_is_questioned(self):
        from bot.middlewares import now_for

        today = now_for("Europe/Moscow").date()
        await self.db.set_reading(USER_ID, today - dt.timedelta(days=1), 203000)
        await self.send("пробег 202000")
        self.assertIn("больше", self.said())

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


class FuelTest(unittest.TestCase):
    """Литры: их пишут рядом с числом, и это не рубли и не дата."""

    def test_litres_are_found(self):
        from bot.car.parsing import parse_litres

        for text, litres in (
            ("заправка 13.2л", 13.2),
            ("бензин 31,73 л", 31.73),
            ("аи-92 40 литров", 40.0),
        ):
            with self.subTest(text=text):
                self.assertAlmostEqual(parse_litres(text), litres)

    def test_no_litres(self):
        from bot.car.parsing import parse_litres

        self.assertIsNone(parse_litres("кофе 300"))
        self.assertIsNone(parse_litres("заправка 900л"))  # столько в бак не влезет

    def test_fuel_words(self):
        from bot.car.parsing import looks_like_fuel

        self.assertTrue(looks_like_fuel("-833 заправка 13.2л"))
        self.assertTrue(looks_like_fuel("бензин аи-92"))
        self.assertFalse(looks_like_fuel("молоко 2л"))


class FuelFlowTest(BotTestCase):
    def said(self) -> str:
        return "\n".join(self.bot.texts)

    async def test_litres_are_not_a_date_and_not_a_price(self):
        """«-833 заправка 13.2л» — это 833 ₽ за 13,2 л, а не 13 февраля."""
        from bot.middlewares import now_for

        today = now_for("Europe/Moscow").date()
        await self.send("-833 заправка 13.2л")

        transaction = (await self.db.last_transactions(USER_ID))[0]
        self.assertEqual(transaction.amount, 83300)
        self.assertEqual(transaction.happened_on, today)
        self.assertIn("13.2", transaction.note)

    async def test_fuel_is_remembered_with_litres(self):
        await self.send("-833 заправка 13.2л")
        self.assertIn("13,2 л", self.said())
        self.assertIn("63", self.said())  # 833 / 13,2 ≈ 63 ₽ за литр

        from bot.middlewares import now_for

        today = now_for("Europe/Moscow").date()
        fills = await self.db.fuel_between(USER_ID, today, today)
        self.assertEqual(len(fills), 1)
        self.assertAlmostEqual(fills[0].litres, 13.2)

    async def test_plain_purchase_is_not_a_fill(self):
        await self.send("молоко 2л 120")
        from bot.middlewares import now_for

        today = now_for("Europe/Moscow").date()
        self.assertEqual(await self.db.fuel_between(USER_ID, today, today), [])

    async def test_consumption_needs_two_fills(self):
        from bot.car.stats import fuel_lines
        from bot.db import UserSettings

        user = UserSettings(user_id=USER_ID, tz="Europe/Moscow", currency="₽")
        start, end = dt.date(2026, 8, 1), dt.date(2026, 8, 31)

        await self.db.set_reading(USER_ID, dt.date(2026, 8, 10), 203000)
        await self.db.add_fuel(USER_ID, dt.date(2026, 8, 10), 40, 250000)
        lines = await fuel_lines(self.db, user, start, end)
        self.assertTrue(any("40,0 л" in line for line in lines))
        self.assertFalse(any("Расход" in line for line in lines))

        # вторая заправка через 500 км: 40 литров на 500 км — это 8 л на сотню
        await self.db.set_reading(USER_ID, dt.date(2026, 8, 20), 203500)
        await self.db.add_fuel(USER_ID, dt.date(2026, 8, 20), 40, 250000)
        lines = await fuel_lines(self.db, user, start, end)
        self.assertTrue(any("8,0 л на 100 км" in line for line in lines))


class FuelReportTest(unittest.IsolatedAsyncioTestCase):
    """Отдельный разговор про топливо: сколько, почём и как менялось."""

    async def asyncSetUp(self):
        self.db = memory_db()
        await self.db.connect()
        await self.db.ensure_user(USER.user_id)

    async def asyncTearDown(self):
        await self.db.close()

    async def report(self):
        from bot.car.stats import build_fuel_report

        return await build_fuel_report(self.db, USER, TODAY)

    async def test_empty(self):
        self.assertIn("Заправок пока нет", await self.report())

    async def test_one_fill_cannot_tell_the_consumption(self):
        await self.db.add_fuel(USER.user_id, dt.date(2026, 8, 17), 31.73, 199900)
        text = await self.report()
        self.assertIn("31,7 л", text)
        self.assertIn("63", text)  # ₽ за литр
        self.assertIn("со второй заправки", text)

    async def test_prices_and_consumption(self):
        await self.db.set_reading(USER.user_id, dt.date(2026, 8, 10), 203000)
        await self.db.add_fuel(USER.user_id, dt.date(2026, 8, 10), 40, 240000)
        await self.db.set_reading(USER.user_id, dt.date(2026, 8, 20), 203500)
        await self.db.add_fuel(USER.user_id, dt.date(2026, 8, 20), 40, 260000)

        text = await self.report()
        self.assertIn("2 заправки", text)
        self.assertIn("80,0 л", text)
        self.assertIn("Дешевле всего", text)
        self.assertIn("8,0 л на 100 км", text)
        self.assertIn("12,5 км", text)  # на литре
        self.assertIn("+8%", text)  # 60 → 65 ₽ за литр

    async def test_impossible_consumption_is_not_shown(self):
        """Забытая заправка даёт литр на сотню — молчать честнее."""
        await self.db.set_reading(USER.user_id, dt.date(2026, 7, 15), 199000)
        await self.db.add_fuel(USER.user_id, dt.date(2026, 7, 15), 32, 195000)
        await self.db.set_reading(USER.user_id, dt.date(2026, 8, 17), 203116)
        await self.db.add_fuel(USER.user_id, dt.date(2026, 8, 17), 31.73, 199900)

        text = await self.report()
        self.assertNotIn("л на 100 км", text)
        self.assertIn("не записана", text)

    async def test_old_notes_are_picked_up(self):
        """Литры писались и раньше — историю жалко терять."""
        from bot.money.db import EXPENSE

        category = await self.db.find_category_by_name(USER.user_id, "Транспорт", EXPENSE)
        await self.db.add_transaction(
            USER.user_id, EXPENSE, 199900, "бензин АИ-92, 31,73 л, пробег 203116",
            dt.date(2026, 8, 17), category.id,
        )
        await self.db.add_transaction(
            USER.user_id, EXPENSE, 30000, "кофе", dt.date(2026, 8, 18), None
        )

        self.assertEqual(await self.db.import_fuel_from_notes(USER.user_id), 1)
        self.assertEqual(await self.db.count_fuel(USER.user_id), 1)

        # повторный проход ничего не задваивает
        self.assertEqual(await self.db.import_fuel_from_notes(USER.user_id), 0)


class ServiceTest(unittest.TestCase):
    """Правила молчания: про ТО слышно, только когда оно близко."""

    def line(self, due, km, interval=10000):
        from bot.car.db import Service
        from bot.car.service import line

        return line(Service(due, interval), km)

    def test_far_away_is_silent(self):
        self.assertEqual(self.line(213000, 203000), "")

    def test_close_is_announced(self):
        self.assertIn("осталось", self.line(204000, 203600))

    def test_overdue_says_by_how_much(self):
        text = self.line(203000, 203600)
        self.assertIn("просрочено", text)
        self.assertIn("600", text)

    def test_no_plan_is_silent(self):
        from bot.car.service import line

        self.assertEqual(line(None, 203000), "")

    def test_buttons_are_round_numbers(self):
        from bot.car.service import targets

        self.assertEqual(targets(203116), [(5000, 208000), (10000, 213000), (15000, 218000)])

    def test_next_after_done_needs_an_interval(self):
        from bot.car.db import Service
        from bot.car.service import next_after_done

        self.assertEqual(next_after_done(Service(203000, 10000), 203100), 213100)
        self.assertIsNone(next_after_done(Service(203000, 0), 203100))


class ServiceFlowTest(BotTestCase):
    def said(self) -> str:
        return "\n".join(self.bot.texts)

    async def test_asks_once_and_then_keeps_quiet(self):
        """Вопрос про ТО не должен приходить каждое утро."""
        await self.send("пробег 203116")
        self.assertIn("Когда ближайшее ТО", self.said())
        self.assertTrue(any("через 10 000" in b for b in self.bot.last_buttons))

        self.bot.calls.clear()
        await self.send("пробег 203200")
        self.assertNotIn("Когда ближайшее ТО", self.said())

    async def test_setting_by_button(self):
        await self.send("пробег 203116")
        await self.click("car:to:213000")

        plan = await self.db.get_service(USER_ID)
        self.assertEqual(plan.due_km, 213000)
        self.assertEqual(plan.interval_km, 9884)
        self.assertIn("До тех пор молчу", self.bot.edits[-1])

    async def test_setting_by_number(self):
        await self.send("пробег 203116")
        await self.click("car:to:ask")
        await self.send("210000")

        self.assertEqual((await self.db.get_service(USER_ID)).due_km, 210000)

    async def test_number_must_be_ahead(self):
        await self.send("пробег 203116")
        await self.click("car:to:ask")
        await self.send("200000")

        self.assertIn("меньше текущего", self.said())
        self.assertIsNone(await self.db.get_service(USER_ID))

    async def test_never_mutes_the_question(self):
        await self.send("пробег 203116")
        await self.click("car:to:never")

        self.bot.calls.clear()
        await self.send("пробег 203200")
        self.assertNotIn("Когда ближайшее ТО", self.said())

    async def test_warns_only_when_close(self):
        await self.db.set_service(USER_ID, 213000, 10000)

        await self.send("пробег 203116")
        self.assertNotIn("До ТО осталось", self.said())

        self.bot.calls.clear()
        await self.send("пробег 212500")
        self.assertIn("До ТО осталось", self.said())
        self.assertIn("500 км", self.said())
        self.assertTrue(any("ТО сделано" in b for b in self.bot.last_buttons))

    async def test_done_sets_the_next_one(self):
        await self.db.set_service(USER_ID, 204000, 10000)
        await self.send("пробег 204100")
        await self.click("car:to:done")

        plan = await self.db.get_service(USER_ID)
        self.assertEqual(plan.due_km, 214100)
        self.assertIn("Следующее", self.said())

    async def test_report_shows_the_plan(self):
        await self.db.set_service(USER_ID, 213000, 10000)
        await self.send("пробег 203000")

        self.assertIn("ТО на 213 000 км", await self.send("/car"))


if __name__ == "__main__":
    unittest.main()
