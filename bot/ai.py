"""Разбор сложных фраз и голоса через Gemini.

Слой сугубо вспомогательный: без ключа `available()` вернёт False, и бот
работает ровно как раньше. Любая ошибка сети или невнятный ответ модели —
это `None`, а не исключение: измерение не должно теряться из-за того, что
чужой сервис прилёг.

Почему Gemini: у него бесплатный тариф без карты и приём аудио прямо в
запросе, поэтому голосовое сообщение не требует отдельного распознавания.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import aiohttp

from . import metrics

logger = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

#: Больше — модель фантазирует, а не разбирает сообщение.
MAX_ITEMS = 10

#: Границы правдоподобия: за ними это не измерение, а промах распознавания.
SYS_RANGE = (60, 260)
DIA_RANGE = (30, 200)
PULSE_RANGE = (25, 220)

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "transcript": {"type": "STRING"},
        "measurements": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "systolic": {"type": "INTEGER"},
                    "diastolic": {"type": "INTEGER"},
                    "pulse": {"type": "INTEGER"},
                    "measured_at": {"type": "STRING"},
                    "note": {"type": "STRING"},
                },
                "required": ["systolic", "diastolic", "measured_at"],
            },
        },
        "metrics": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING"},
                    "value": {"type": "NUMBER"},
                    "date": {"type": "STRING"},
                },
                "required": ["kind", "value", "date"],
            },
        },
    },
    "required": ["transcript", "measurements", "metrics"],
}

PROMPT = """Ты — разборщик записей в телеграм-боте «дневник давления».

Сейчас {now} ({weekday}).

Из сообщения пользователя вытащи измерения давления и показатели здоровья.

Измерение (measurements):
- systolic — верхнее давление, diastolic — нижнее, целые числа.
- pulse — пульс, если назван; иначе не указывай.
- measured_at — время измерения в формате ГГГГ-ММ-ДД ЧЧ:ММ. «утром» — 08:00,
  «вечером» — 21:00, «днём» — 14:00, «ночью» — 03:00. Если время не названо,
  ставь текущее. «вчера», «в пятницу» переводи в дату.
- note — короткий комментарий пользователя, если он есть («после кофе»,
  «болела голова»). Иначе пустая строка.

Показатели (metrics), kind строго одно из:
- sleep — сон, value в часах (7.5 = 7 часов 30 минут)
- steps — шаги за день, value штук
- resting_pulse — пульс покоя с браслета (не путать с пульсом на замере)
- weight — вес, value в килограммах
date — дата показателя в формате ГГГГ-ММ-ДД.

Если чего-то в сообщении нет — верни пустой список. Ничего не выдумывай и не
достраивай: пропущенное нижнее давление не угадывай.
transcript — для текста пустая строка, для аудио: что было сказано."""

INSIGHT_PROMPT = """Ты — помощник в дневнике артериального давления.

Вот выжимка из дневника пользователя:

{summary}

Напиши 4–6 коротких предложений живым русским языком: что видно в этих
цифрах — какие значения обычны, есть ли разница между утром и вечером, что
изменилось за период, заметны ли всплески.

Строгие правила:
- Ты не врач. Никаких диагнозов, никаких советов про лекарства, дозировки и
  образ жизни. Только то, что видно в числах.
- Если цифры высокие, скажи об этом фактом и добавь, что это разговор с
  врачом, а не повод для самолечения.
- Только то, что есть в данных, без выдумок. Без markdown и списков.
Не здоровайся и не прощайся."""


@dataclass(frozen=True)
class AiMeasurement:
    systolic: int
    diastolic: int
    pulse: Optional[int]
    measured_at: dt.datetime
    note: str


@dataclass(frozen=True)
class AiMetric:
    kind: str
    value: float  # в единицах хранения: сон — минуты
    on_date: dt.date


@dataclass(frozen=True)
class AiResult:
    transcript: str
    measurements: tuple[AiMeasurement, ...]
    metrics: tuple[AiMetric, ...]

    def __bool__(self) -> bool:
        return bool(self.measurements or self.metrics)


def _in_range(value: Any, bounds: tuple[int, int]) -> Optional[int]:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return number if bounds[0] <= number <= bounds[1] else None


def _to_moment(value: Any, now: dt.datetime) -> dt.datetime:
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = dt.datetime.strptime(str(value), pattern)
        except (TypeError, ValueError):
            continue
        # Будущее — почти всегда промах модели с датой.
        return now if parsed > now else parsed
    return now


def _to_day(value: Any, now: dt.datetime) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return now.date()
    return now.date() if parsed > now.date() else parsed


class AiClient:
    def __init__(
        self,
        api_key: str = "",
        model: str = "gemini-2.5-flash",
        timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model
        self._timeout = timeout

    def available(self) -> bool:
        return bool(self._api_key)

    # ------------------------------------------------------------- транспорт

    async def _call(self, parts: list[dict], schema: Optional[dict] = None) -> Optional[str]:
        """Один запрос к модели. Возвращает текст ответа или None при любой беде."""
        if not self.available():
            return None

        payload: dict[str, Any] = {"contents": [{"parts": parts}]}
        if schema is not None:
            payload["generationConfig"] = {
                "responseMimeType": "application/json",
                "responseSchema": schema,
            }

        url = API_URL.format(model=self._model)
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url, params={"key": self._api_key}, json=payload
                ) as response:
                    if response.status != 200:
                        body = (await response.text())[:300]
                        logger.warning("Gemini ответил %s: %s", response.status, body)
                        return None
                    data = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Не достучался до Gemini: %s", exc)
            return None

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            logger.warning("Неожиданный ответ Gemini: %s", str(data)[:300])
            return None

    # --------------------------------------------------------------- разбор

    def _prompt(self, now: dt.datetime) -> str:
        weekdays = (
            "понедельник", "вторник", "среда", "четверг",
            "пятница", "суббота", "воскресенье",
        )
        return PROMPT.format(
            now=now.strftime("%Y-%m-%d %H:%M"), weekday=weekdays[now.weekday()]
        )

    def _parse(self, raw: str, now: dt.datetime) -> Optional[AiResult]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Gemini вернул не JSON: %s", raw[:200])
            return None
        if not isinstance(data, dict):
            return None

        measurements: list[AiMeasurement] = []
        for item in (data.get("measurements") or [])[:MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            systolic = _in_range(item.get("systolic"), SYS_RANGE)
            diastolic = _in_range(item.get("diastolic"), DIA_RANGE)
            if systolic is None or diastolic is None or systolic <= diastolic:
                continue
            measurements.append(
                AiMeasurement(
                    systolic=systolic,
                    diastolic=diastolic,
                    pulse=_in_range(item.get("pulse"), PULSE_RANGE),
                    measured_at=_to_moment(item.get("measured_at"), now),
                    note=str(item.get("note") or "").strip()[:200],
                )
            )

        parsed_metrics: list[AiMetric] = []
        for item in (data.get("metrics") or [])[:MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            kind = metrics.kind_of(str(item.get("kind") or "").strip())
            if kind is None:
                continue
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                continue
            if kind.key == metrics.SLEEP.key:
                value *= 60  # модель отвечает в часах, храним минуты
            if not kind.low <= value <= kind.high:
                continue
            parsed_metrics.append(
                AiMetric(kind=kind.key, value=value, on_date=_to_day(item.get("date"), now))
            )

        return AiResult(
            transcript=str(data.get("transcript") or "").strip(),
            measurements=tuple(measurements),
            metrics=tuple(parsed_metrics),
        )

    async def extract_from_text(self, text: str, now: dt.datetime) -> Optional[AiResult]:
        parts = [
            {"text": self._prompt(now)},
            {"text": f"Сообщение пользователя:\n{text}"},
        ]
        raw = await self._call(parts, EXTRACT_SCHEMA)
        return self._parse(raw, now) if raw else None

    async def extract_from_audio(
        self, audio: bytes, mime_type: str, now: dt.datetime
    ) -> Optional[AiResult]:
        parts = [
            {"text": self._prompt(now)},
            {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(audio).decode("ascii"),
                }
            },
            {"text": "Разбери голосовое сообщение выше."},
        ]
        raw = await self._call(parts, EXTRACT_SCHEMA)
        return self._parse(raw, now) if raw else None

    async def insight(self, summary: str) -> Optional[str]:
        raw = await self._call([{"text": INSIGHT_PROMPT.format(summary=summary)}])
        if not raw:
            return None
        return raw.strip()[:2000] or None


def kinds_hint() -> Sequence[str]:
    """Ключи показателей — чтобы промпт и разбор не разъезжались."""
    return tuple(kind.key for kind in metrics.ALL_KINDS)
