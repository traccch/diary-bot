"""Тесты ИИ-слоя. Сеть не трогаем: транспорт подменяется заглушкой."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import unittest
from unittest import mock

import aiohttp

from bot import ai, metrics
from bot.ai import AiClient, _in_range, _to_day, _to_moment

NOW = dt.datetime(2026, 8, 17, 9, 30)


def gemini_answer(payload: dict | str) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None, text: str = "") -> None:
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, params=None, json=None):
        self.requests.append((url, {"params": params, "json": json}))
        if self._error is not None:
            raise self._error
        return self._response


class ConversionTest(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(_in_range(120, ai.SYS_RANGE), 120)
        self.assertEqual(_in_range("135", ai.SYS_RANGE), 135)
        self.assertIsNone(_in_range(400, ai.SYS_RANGE))
        self.assertIsNone(_in_range(None, ai.SYS_RANGE))

    def test_moments(self):
        self.assertEqual(
            _to_moment("2026-08-16 21:30", NOW), dt.datetime(2026, 8, 16, 21, 30)
        )
        # будущее и мусор — «сейчас»
        self.assertEqual(_to_moment("2027-01-01 10:00", NOW), NOW)
        self.assertEqual(_to_moment("вчера вечером", NOW), NOW)

    def test_days(self):
        self.assertEqual(_to_day("2026-08-15", NOW), dt.date(2026, 8, 15))
        self.assertEqual(_to_day("2030-01-01", NOW), NOW.date())


class WithoutKeyTest(unittest.IsolatedAsyncioTestCase):
    async def test_client_is_inert(self):
        client = AiClient(api_key="")
        self.assertFalse(client.available())
        self.assertIsNone(await client.extract_from_text("120 на 80", NOW))
        self.assertIsNone(await client.insight("сводка"))


class ExtractionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AiClient(api_key="test-key", model="gemini-2.5-flash")

    async def extract(self, payload):
        session = FakeSession(FakeResponse(200, gemini_answer(payload)))
        with mock.patch.object(ai.aiohttp, "ClientSession", session):
            result = await self.client.extract_from_text("сто тридцать на восемьдесят", NOW)
        self.session = session
        return result

    async def test_measurement(self):
        result = await self.extract(
            {
                "transcript": "",
                "measurements": [
                    {
                        "systolic": 130,
                        "diastolic": 85,
                        "pulse": 68,
                        "measured_at": "2026-08-17 08:00",
                        "note": "после кофе",
                    }
                ],
                "metrics": [],
            }
        )
        self.assertEqual(len(result.measurements), 1)
        measurement = result.measurements[0]
        self.assertEqual((measurement.systolic, measurement.diastolic), (130, 85))
        self.assertEqual(measurement.pulse, 68)
        self.assertEqual(measurement.note, "после кофе")
        self.assertTrue(result)

    async def test_nonsense_is_dropped(self):
        result = await self.extract(
            {
                "transcript": "",
                "measurements": [
                    {"systolic": 80, "diastolic": 120, "measured_at": "2026-08-17 08:00"},
                    {"systolic": 400, "diastolic": 200, "measured_at": "2026-08-17 08:00"},
                    {"systolic": 120, "diastolic": 80, "measured_at": "2026-08-17 08:00"},
                ],
                "metrics": [],
            }
        )
        # перевёрнутое и невозможное отброшено, осталось одно
        self.assertEqual(len(result.measurements), 1)
        self.assertEqual(result.measurements[0].systolic, 120)

    async def test_sleep_is_stored_in_minutes(self):
        result = await self.extract(
            {
                "transcript": "",
                "measurements": [],
                "metrics": [
                    {"kind": "sleep", "value": 7.5, "date": "2026-08-17"},
                    {"kind": "steps", "value": 8200, "date": "2026-08-17"},
                ],
            }
        )
        by_kind = {item.kind: item.value for item in result.metrics}
        self.assertEqual(by_kind[metrics.SLEEP.key], 450)  # 7,5 часа → минуты
        self.assertEqual(by_kind[metrics.STEPS.key], 8200)

    async def test_unknown_metric_and_out_of_range(self):
        result = await self.extract(
            {
                "transcript": "",
                "measurements": [],
                "metrics": [
                    {"kind": "настроение", "value": 5, "date": "2026-08-17"},
                    {"kind": "weight", "value": 900, "date": "2026-08-17"},
                ],
            }
        )
        self.assertEqual(result.metrics, ())
        self.assertFalse(result)

    async def test_request_shape(self):
        await self.extract({"transcript": "", "measurements": [], "metrics": []})
        url, kwargs = self.session.requests[0]
        self.assertIn("gemini-2.5-flash:generateContent", url)
        self.assertEqual(kwargs["params"], {"key": "test-key"})
        prompt = kwargs["json"]["contents"][0]["parts"][0]["text"]
        self.assertIn("2026-08-17 09:30", prompt)
        self.assertIn("resting_pulse", prompt)

    async def test_not_json(self):
        self.assertIsNone(await self.extract("не понял вас"))


class TransportTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AiClient(api_key="test-key")

    async def call(self, session):
        with mock.patch.object(ai.aiohttp, "ClientSession", session):
            return await self.client.insight("сводка")

    async def test_http_error(self):
        self.assertIsNone(await self.call(FakeSession(FakeResponse(429, text="quota"))))

    async def test_network_error(self):
        self.assertIsNone(await self.call(FakeSession(error=aiohttp.ClientError("нет сети"))))

    async def test_timeout(self):
        self.assertIsNone(await self.call(FakeSession(error=asyncio.TimeoutError())))

    async def test_unexpected_body(self):
        self.assertIsNone(await self.call(FakeSession(FakeResponse(200, {"candidates": []}))))

    async def test_text_answer(self):
        session = FakeSession(FakeResponse(200, gemini_answer("Утром выше, чем вечером.")))
        self.assertEqual(await self.call(session), "Утром выше, чем вечером.")


class AudioTest(unittest.IsolatedAsyncioTestCase):
    async def test_audio_goes_as_inline_data(self):
        client = AiClient(api_key="test-key")
        session = FakeSession(
            FakeResponse(
                200,
                gemini_answer(
                    {
                        "transcript": "сто двадцать на восемьдесят",
                        "measurements": [
                            {"systolic": 120, "diastolic": 80,
                             "measured_at": "2026-08-17 09:00"}
                        ],
                        "metrics": [],
                    }
                ),
            )
        )
        with mock.patch.object(ai.aiohttp, "ClientSession", session):
            result = await client.extract_from_audio(b"OggS-fake", "audio/ogg", NOW)

        self.assertEqual(result.transcript, "сто двадцать на восемьдесят")
        self.assertEqual(result.measurements[0].diastolic, 80)

        parts = session.requests[0][1]["json"]["contents"][0]["parts"]
        inline = next(part for part in parts if "inline_data" in part)["inline_data"]
        self.assertEqual(inline["mime_type"], "audio/ogg")
        self.assertTrue(inline["data"])


if __name__ == "__main__":
    unittest.main()
