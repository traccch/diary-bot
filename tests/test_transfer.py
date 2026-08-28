"""Загрузка операций файлом: разбор, предпросмотр, запись."""

from __future__ import annotations

import datetime as dt
import json
import unittest

from bot.money import transfer
from bot.money.db import EXPENSE, INCOME

from .test_handlers import USER_ID, BotTestCase

TODAY = dt.date(2026, 8, 27)


def payload(*rows) -> bytes:
    return json.dumps({"transactions": list(rows)}, ensure_ascii=False).encode("utf-8")


ROWS = (
    {"date": "2026-08-11", "amount": -661, "note": "продукты"},
    {"date": "2026-08-12", "amount": 215, "note": "кешбэк"},
)


class ParseTest(unittest.TestCase):
    def test_sign_decides_kind(self):
        plan = transfer.parse(payload(*ROWS), TODAY)
        self.assertEqual(len(plan.rows), 2)
        self.assertEqual(plan.rows[0].kind, EXPENSE)
        self.assertEqual(plan.rows[0].amount, 66100)
        self.assertEqual(plan.rows[1].kind, INCOME)

    def test_totals_and_period(self):
        plan = transfer.parse(payload(*ROWS), TODAY)
        self.assertEqual(plan.expense, 66100)
        self.assertEqual(plan.income, 21500)
        self.assertEqual(plan.period, (dt.date(2026, 8, 11), dt.date(2026, 8, 12)))

    def test_kopecks_and_separators(self):
        plan = transfer.parse(
            payload({"amount": "-1 234,50", "date": "2026-08-11", "note": "х"}), TODAY
        )
        self.assertEqual(plan.rows[0].amount, 123450)

    def test_date_defaults_to_today(self):
        plan = transfer.parse(payload({"amount": -100, "note": "кофе"}), TODAY)
        self.assertEqual(plan.rows[0].happened_on, TODAY)

    def test_garbage_rows_are_counted(self):
        plan = transfer.parse(
            payload(
                {"amount": "много", "note": "ерунда"},
                {"amount": 0, "note": "ноль"},
                {"date": "вчера", "amount": -10, "note": "х"},
                *ROWS,
            ),
            TODAY,
        )
        self.assertEqual(plan.skipped, 3)
        self.assertEqual(len(plan.rows), 2)

    def test_bare_list_is_accepted(self):
        raw = json.dumps(list(ROWS), ensure_ascii=False).encode("utf-8")
        self.assertEqual(len(transfer.parse(raw, TODAY).rows), 2)

    def test_bad_files_are_rejected_clearly(self):
        for raw, hint in (
            ("не json".encode("utf-8"), "не JSON"),
            (b'{"foo": 1}', "transactions"),
            ('{"transactions": "строка"}'.encode("utf-8"), "transactions"),
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(transfer.ImportError_) as caught:
                    transfer.parse(raw, TODAY)
                self.assertIn(hint, str(caught.exception))

    def test_too_many_rows(self):
        many = [{"amount": -1} for _ in range(transfer.MAX_ROWS + 1)]
        with self.assertRaises(transfer.ImportError_):
            transfer.parse(json.dumps({"transactions": many}).encode("utf-8"), TODAY)

    def test_bom_survives(self):
        self.assertEqual(len(transfer.parse(b"\xef\xbb\xbf" + payload(*ROWS), TODAY).rows), 2)

    def test_dump_round_trip(self):
        plan = transfer.parse(payload(*ROWS), TODAY)
        again = transfer.parse(transfer.dump(plan.rows), TODAY)
        self.assertEqual(plan.rows, again.rows)


class ImportFlowTest(BotTestCase):
    async def test_preview_then_write(self):
        await self.send_document(payload(*ROWS))
        preview = self.bot.texts[-1]
        self.assertIn("2 операции", preview)
        self.assertIn("661", preview)
        self.assertIn("с 11.08 по 12.08", preview)
        self.assertEqual(await self.db.count_transactions(USER_ID), 0)  # пока ничего

        await self.click("imp:apply")
        self.assertIn("Записал 2", self.bot.edits[-1])
        self.assertEqual(await self.db.count_transactions(USER_ID), 2)

    async def test_category_is_matched_by_note(self):
        await self.send_document(payload(*ROWS))
        await self.click("imp:apply")

        stored = await self.db.last_transactions(USER_ID)
        by_note = {item.note: item for item in stored}
        self.assertEqual(by_note["продукты"].category_name, "Продукты")

    async def test_explicit_category_beats_the_note(self):
        """«электроэнергия, долг за три месяца» — это коммуналка, а не долг."""
        await self.send_document(
            payload({
                "date": "2026-08-16",
                "amount": -2080,
                "note": "электроэнергия, долг за несколько месяцев",
                "category": "Жильё",
            })
        )
        await self.click("imp:apply")

        stored = (await self.db.last_transactions(USER_ID))[0]
        self.assertEqual(stored.category_name, "Жильё")

    async def test_unknown_category_falls_back_to_the_note(self):
        await self.send_document(
            payload({"date": "2026-08-16", "amount": -300, "note": "кофе",
                     "category": "Криптовалюты"})
        )
        await self.click("imp:apply")
        self.assertEqual((await self.db.last_transactions(USER_ID))[0].category_name, "Кафе")

    async def test_second_import_does_not_double(self):
        await self.send_document(payload(*ROWS))
        await self.click("imp:apply")

        await self.send_document(payload(*ROWS))
        self.assertIn("уже есть", self.bot.texts[-1])
        self.assertEqual(await self.db.count_transactions(USER_ID), 2)

    async def test_same_file_with_categories_fixes_them(self):
        """Файл, присланный второй раз, — это правка, а не дубль."""
        await self.send_document(payload({"date": "2026-08-11", "amount": -2080,
                                          "note": "электроэнергия, долг за три месяца"}))
        await self.click("imp:apply")
        stored = (await self.db.last_transactions(USER_ID))[0]
        self.assertEqual(stored.category_name, "Долги")  # заметка обманула

        await self.send_document(payload({"date": "2026-08-11", "amount": -2080,
                                          "note": "электроэнергия, долг за три месяца",
                                          "category": "Жильё"}))
        preview = self.bot.texts[-1]
        self.assertIn("Поправлю категорию", preview)
        self.assertIn("Долги → <b>Жильё</b>", preview)

        await self.click("imp:apply")
        self.assertIn("поправил категорию у 1", self.bot.edits[-1].lower())
        self.assertEqual(await self.db.count_transactions(USER_ID), 1)  # не задвоилось
        fixed = (await self.db.last_transactions(USER_ID))[0]
        self.assertEqual(fixed.category_name, "Жильё")

    async def test_same_category_is_still_a_duplicate(self):
        rows = ({"date": "2026-08-11", "amount": -661, "note": "продукты",
                 "category": "Продукты"},)
        await self.send_document(payload(*rows))
        await self.click("imp:apply")

        await self.send_document(payload(*rows))
        self.assertIn("уже есть", self.bot.texts[-1])

    async def test_repeated_rows_inside_one_file(self):
        """Две одинаковые строки в файле — это одна операция, а не правка."""
        row = {"date": "2026-08-11", "amount": -661, "note": "продукты",
               "category": "Продукты"}
        await self.send_document(payload(row, row))
        await self.click("imp:apply")
        self.assertEqual(await self.db.count_transactions(USER_ID), 1)

    async def test_fix_keeps_the_kind(self):
        """«Долги» есть и в расходах, и в доходах — это разные категории."""
        await self.send_document(payload({"date": "2026-08-25", "amount": 1000,
                                          "note": "займ у мамы", "category": "Прочее"}))
        await self.click("imp:apply")

        await self.send_document(payload({"date": "2026-08-25", "amount": 1000,
                                          "note": "займ у мамы", "category": "Долги"}))
        await self.click("imp:apply")

        fixed = (await self.db.last_transactions(USER_ID))[0]
        self.assertEqual(fixed.category_name, "Долги")
        self.assertTrue(fixed.is_income)
        category = await self.db.get_category(USER_ID, fixed.category_id)
        self.assertEqual(category.kind, "income")

    async def test_long_list_can_be_unfolded(self):
        """«…и ещё 38» — плохая концовка для списка, который надо проверить."""
        many = tuple(
            {"date": "2026-08-11", "amount": -(100 + i), "note": f"покупка {i}"}
            for i in range(20)
        )
        await self.send_document(payload(*many))
        self.assertIn("…и ещё", self.bot.texts[-1])
        self.assertTrue(any("Показать все" in text for text in self.bot.last_buttons))

        before = len(self.bot.texts)
        await self.click("imp:more")
        shown = "\n".join(self.bot.texts[before:])
        self.assertIn("покупка 0", shown)
        self.assertIn("покупка 19", shown)
        self.assertNotIn("…и ещё", shown)

    async def test_very_long_list_is_split_into_messages(self):
        many = tuple(
            {"date": "2026-08-11", "amount": -(1000 + i),
             "note": f"довольно длинная заметка про покупку номер {i} в магазине"}
            for i in range(120)
        )
        await self.send_document(payload(*many))
        before = len(self.bot.texts)
        await self.click("imp:more")

        parts = self.bot.texts[before:]
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 4096 for part in parts))

    async def test_import_opens_the_money_section(self):
        """Иначе следующая /stats покажет давление, а не траты."""
        await self.send_document(payload(*ROWS))
        await self.click("imp:apply")

        user = await self.db.ensure_user(USER_ID)
        self.assertEqual(user.section, "money")
        self.assertTrue(any("Сводка" in text for text in self.bot.last_buttons))

    async def test_cancel_writes_nothing(self):
        await self.send_document(payload(*ROWS))
        await self.click("imp:cancel")
        self.assertIn("Ничего не записал", self.bot.edits[-1])
        self.assertEqual(await self.db.count_transactions(USER_ID), 0)

    async def test_wrong_file_type(self):
        await self.send_document(b"1,2,3", name="table.csv")
        self.assertIn(".json", self.bot.texts[-1])

    async def test_broken_file_is_explained(self):
        await self.send_document(b"{}")
        self.assertIn("transactions", self.bot.texts[-1])

    async def test_how_to_is_reachable_by_command_and_button(self):
        self.assertIn("transactions", await self.send("/import"))
        await self.click("do:money:import")
        self.assertIn("transactions", self.bot.texts[-1])


if __name__ == "__main__":
    unittest.main()
