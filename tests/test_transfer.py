"""Тесты обмена с внешним ИИ: выгрузка JSON и разбор правленого файла."""

from __future__ import annotations

import datetime as dt
import json
import unittest

from bot import transfer
from bot.db import Measurement, Metric

NOW = dt.datetime(2026, 8, 17, 9, 30)


def measurement(id: int, systolic: int, diastolic: int, pulse=None, hour: int = 8) -> Measurement:
    return Measurement(
        id=id,
        systolic=systolic,
        diastolic=diastolic,
        pulse=pulse,
        measured_at=dt.datetime(2026, 8, 16, hour, 0),
        note="",
    )


EXISTING = [measurement(1, 120, 80, 68), measurement(2, 180, 85, 72, hour=21)]
HEALTH = [Metric(kind="sleep", on_date=dt.date(2026, 8, 16), value=450)]


class DumpTest(unittest.TestCase):
    def payload(self) -> dict:
        raw = transfer.dump(EXISTING, HEALTH, (135, 85), "Europe/Moscow", NOW)
        return json.loads(raw.decode("utf-8"))

    def test_shape(self):
        data = self.payload()
        self.assertEqual(data["format"], transfer.FORMAT)
        self.assertEqual(data["target"], {"systolic": 135, "diastolic": 85})
        self.assertIn("_instructions", data)

    def test_measurements_and_metrics(self):
        data = self.payload()
        first = data["measurements"][0]
        self.assertEqual((first["systolic"], first["diastolic"], first["pulse"]), (120, 80, 68))
        self.assertEqual(first["measured_at"], "2026-08-16 08:00")
        # сон хранится в минутах, а выгружается в часах — как на графике
        self.assertEqual(data["metrics"][0]["value"], 7.5)


def edited(**changes) -> bytes:
    data = json.loads(
        transfer.dump(EXISTING, HEALTH, (135, 85), "Europe/Moscow", NOW).decode("utf-8")
    )
    for index, patch in changes.items():
        data["measurements"][int(index)].update(patch)
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class ParseTest(unittest.TestCase):
    def test_typo_becomes_update(self):
        # ИИ увидел 180/85 среди 120-х и понял, что нажали 8 вместо 3
        plan = transfer.parse(edited(**{"1": {"systolic": 130}}), EXISTING, NOW)
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(plan.unchanged, 1)

        change = plan.changes[0]
        self.assertEqual((change.kind, change.measurement_id), ("update", 2))
        self.assertEqual(change.systolic, 130)
        self.assertIsNone(change.diastolic)  # остальное не трогаем

    def test_swapped_values_are_skipped(self):
        plan = transfer.parse(
            edited(**{"0": {"systolic": 80, "diastolic": 120}}), EXISTING, NOW
        )
        self.assertEqual(plan.skipped, 1)
        self.assertEqual(plan.of("update"), [])

    def test_impossible_values_are_skipped(self):
        plan = transfer.parse(edited(**{"0": {"systolic": 400}}), EXISTING, NOW)
        self.assertEqual(plan.skipped, 1)

    def test_time_and_note_update(self):
        plan = transfer.parse(
            edited(**{"0": {"measured_at": "2026-08-16 07:15", "note": "после кофе"}}),
            EXISTING,
            NOW,
        )
        change = plan.changes[0]
        self.assertEqual(change.measured_at, dt.datetime(2026, 8, 16, 7, 15))
        self.assertEqual(change.note, "после кофе")

    def test_new_row_becomes_create(self):
        data = json.loads(edited().decode("utf-8"))
        data["measurements"].append(
            {"measured_at": "2026-08-17 08:00", "systolic": 118, "diastolic": 76, "pulse": 64}
        )
        plan = transfer.parse(json.dumps(data).encode("utf-8"), EXISTING, NOW)

        creates = plan.of("create")
        self.assertEqual(len(creates), 1)
        self.assertEqual((creates[0].systolic, creates[0].pulse), (118, 64))

    def test_delete_flag(self):
        plan = transfer.parse(edited(**{"0": {"delete": True}}), EXISTING, NOW)
        self.assertEqual([change.measurement_id for change in plan.of("delete")], [1])

    def test_untouched_file_changes_nothing(self):
        plan = transfer.parse(edited(), EXISTING, NOW)
        self.assertFalse(plan)
        self.assertEqual(plan.unchanged, 2)

    def test_missing_rows_are_not_deleted(self):
        data = json.loads(edited().decode("utf-8"))
        data["measurements"] = data["measurements"][:1]
        plan = transfer.parse(json.dumps(data).encode("utf-8"), EXISTING, NOW)
        self.assertEqual(plan.of("delete"), [])

    def test_bad_files_are_rejected_clearly(self):
        for payload, hint in (
            ("не json вовсе".encode("utf-8"), "не JSON"),
            (b'{"foo": 1}', "measurements"),
            ('{"measurements": "строка"}'.encode("utf-8"), "списком"),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(transfer.ImportError_) as caught:
                    transfer.parse(payload, EXISTING, NOW)
                self.assertIn(hint, str(caught.exception))

    def test_too_many_rows(self):
        data = {"measurements": [{"systolic": 120, "diastolic": 80}] * (transfer.MAX_ROWS + 1)}
        with self.assertRaises(transfer.ImportError_):
            transfer.parse(json.dumps(data).encode("utf-8"), EXISTING, NOW)

    def test_bom_is_survived(self):
        plan = transfer.parse(b"\xef\xbb\xbf" + edited(), EXISTING, NOW)
        self.assertEqual(plan.unchanged, 2)


if __name__ == "__main__":
    unittest.main()
