"""Классификация давления и предупреждения."""

from __future__ import annotations

import unittest

from bot.pressure.classify import alert, classify, in_target


class ClassifyTest(unittest.TestCase):
    def test_scale(self):
        cases = {
            (115, 75): "optimal",
            (119, 79): "optimal",
            (125, 82): "normal",
            (120, 70): "normal",
            (135, 86): "high_normal",
            (129, 87): "high_normal",
            (145, 95): "ag1",
            (139, 92): "ag1",
            (165, 95): "ag2",
            (150, 105): "ag2",
            (185, 95): "ag3",
            (170, 115): "ag3",
            (85, 55): "hypotension",
            (110, 55): "hypotension",
        }
        for (systolic, diastolic), key in cases.items():
            with self.subTest(bp=f"{systolic}/{diastolic}"):
                self.assertEqual(classify(systolic, diastolic).key, key)

    def test_worse_value_decides(self):
        # нижнее в норме, верхнее нет — и наоборот
        self.assertEqual(classify(145, 80).key, "ish")
        self.assertEqual(classify(120, 95).key, "ag1")

    def test_isolated_systolic(self):
        self.assertEqual(classify(150, 85).key, "ish")
        self.assertEqual(classify(190, 80).key, "ish")
        self.assertNotEqual(classify(150, 95).key, "ish")

    def test_every_grade_has_icon_and_titles(self):
        for systolic in range(70, 220, 5):
            for diastolic in range(40, min(systolic, 140), 5):
                grade = classify(systolic, diastolic)
                self.assertTrue(grade.icon and grade.title and grade.short)


class AlertTest(unittest.TestCase):
    def test_crisis(self):
        self.assertIn("103", alert(185, 95) or "")
        self.assertIn("103", alert(160, 125) or "")

    def test_low(self):
        self.assertIn("низкое", alert(84, 60) or "")

    def test_pulse(self):
        self.assertIn("пульс", (alert(120, 80, 130) or "").lower())
        self.assertIn("пульс", (alert(120, 80, 40) or "").lower())

    def test_silent_when_fine(self):
        self.assertIsNone(alert(120, 80, 70))
        self.assertIsNone(alert(145, 92, 80))


class TargetTest(unittest.TestCase):
    def test_in_target(self):
        self.assertTrue(in_target(130, 80, 135, 85))
        self.assertFalse(in_target(136, 80, 135, 85))
        self.assertFalse(in_target(130, 86, 135, 85))


if __name__ == "__main__":
    unittest.main()
