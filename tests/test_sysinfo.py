"""Нагрузка машины: считаем сами, без сторонних библиотек."""

from __future__ import annotations

import unittest
from unittest import mock

from bot import sysinfo


class CpuMeterTest(unittest.TestCase):
    """Мгновенной загрузки не бывает — она считается между двумя замерами."""

    def meter(self, *samples):
        meter = sysinfo.CpuMeter()
        times = iter(samples)
        with mock.patch.object(sysinfo, "_cpu_times", lambda: next(times)):
            return meter, [meter.sample() for _ in samples]

    def test_first_sample_has_nothing_to_compare_with(self):
        _, results = self.meter((100.0, 200.0))
        self.assertEqual(results, [None])

    def test_busy_share_between_samples(self):
        # за интервал прошло 100 единиц, из них 75 в работе
        _, results = self.meter((100.0, 200.0), (175.0, 300.0))
        self.assertEqual(results[1], 75)

    def test_idle_machine(self):
        _, results = self.meter((100.0, 200.0), (100.0, 300.0))
        self.assertEqual(results[1], 0)

    def test_no_movement_is_not_zero_but_unknown(self):
        _, results = self.meter((100.0, 200.0), (100.0, 200.0))
        self.assertIsNone(results[1])

    def test_unavailable_source_is_silent(self):
        meter = sysinfo.CpuMeter()
        with mock.patch.object(sysinfo, "_cpu_times", lambda: None):
            self.assertIsNone(meter.sample())


class MemoryTest(unittest.TestCase):
    def test_used_percent(self):
        self.assertEqual(sysinfo.Memory(8000, 2000).used_percent, 75)
        self.assertEqual(sysinfo.Memory(0, 0).used_percent, 0)

    def test_formatting(self):
        self.assertEqual(sysinfo.format_memory(sysinfo.Memory(8192, 2048)), "8 ГБ · свободно 2,0")
        self.assertEqual(sysinfo.format_memory(None), "")

    def test_real_machine_answers_something(self):
        """На линуксе и винде память узнаётся; на прочих — молчим, но не падаем."""
        state = sysinfo.memory()
        if state is not None:
            self.assertGreater(state.total_mb, 0)
            self.assertLessEqual(state.available_mb, state.total_mb)


class DescribeTest(unittest.TestCase):
    def test_cores_are_declined(self):
        with mock.patch.object(sysinfo, "cores", lambda: 4):
            self.assertIn("4 ядра", sysinfo.describe())
        with mock.patch.object(sysinfo, "cores", lambda: 8):
            self.assertIn("8 ядер", sysinfo.describe())

    def test_survives_when_nothing_is_known(self):
        with mock.patch.object(sysinfo, "memory", lambda: None), mock.patch.object(
            sysinfo, "disk_free_mb", lambda path=".": None
        ):
            self.assertIn("ядр", sysinfo.describe())

    def test_load_line(self):
        meter = sysinfo.CpuMeter()
        with mock.patch.object(sysinfo.CpuMeter, "sample", lambda self: 92), mock.patch.object(
            sysinfo, "memory", lambda: sysinfo.Memory(8000, 2000)
        ):
            self.assertEqual(sysinfo.load_line(meter), "ЦП 92% · память 75%")

    def test_load_line_is_empty_when_nothing_is_known(self):
        meter = sysinfo.CpuMeter()
        with mock.patch.object(sysinfo.CpuMeter, "sample", lambda self: None), mock.patch.object(
            sysinfo, "memory", lambda: None
        ):
            self.assertEqual(sysinfo.load_line(meter), "")


class ProgressLoadTest(unittest.TestCase):
    """Во время обновления нагрузка видна рядом с секундомером."""

    def test_load_is_shown_next_to_the_stopwatch(self):
        from bot.handlers.update import render_progress

        text = render_progress("tests", {"pull", "tests"}, 131, "ЦП 98% · память 82%")
        self.assertIn("2 мин 11 с · ЦП 98%", text)

    def test_no_load_no_clutter(self):
        from bot.handlers.update import render_progress

        self.assertIn("<i>34 с</i>", render_progress("tests", {"tests"}, 34))


if __name__ == "__main__":
    unittest.main()
