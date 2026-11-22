"""Погода и что надеть на прогулку малышу.

Прогноз — Open-Meteo: бесплатно, без ключей и регистрации. Город ищется по
названию там же. Совет строится по ощущаемой температуре (с учётом ветра и
влажности), а не по термометру: малыша продувает так же, как взрослого.

Советы — общие ориентиры для ребёнка до двух лет. Главная проверка всё равно
руками: шея и спинка тёплые и сухие — в самый раз; влажные — перегрели;
прохладная шея — пора добавить слой. Холодные ладошки сами по себе — норма.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence
from urllib.parse import quote

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search?count=1&language=ru&name={name}"
FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"
    "&hourly=temperature_2m,apparent_temperature,precipitation_probability,weather_code,"
    "wind_speed_10m&timezone=auto&forecast_days=1&wind_speed_unit=ms"
)
TIMEOUT_SECONDS = 15


class WeatherError(RuntimeError):
    """Прогноз не получить: сеть, сервис или город не найден."""


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Hour:
    at: dt.datetime
    temp: float
    feels: float
    rain_chance: int
    code: int
    wind: float


@dataclass(frozen=True)
class Forecast:
    temp: float
    feels: float
    precipitation: float
    code: int
    wind: float
    hours: tuple[Hour, ...]


#: Коды погоды WMO → коротко по-русски.
WEATHER_CODES = {
    0: "ясно", 1: "почти ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь", 51: "морось", 53: "морось", 55: "сильная морось",
    56: "ледяная морось", 57: "ледяная морось", 61: "небольшой дождь", 63: "дождь",
    65: "ливень", 66: "ледяной дождь", 67: "ледяной дождь", 71: "небольшой снег",
    73: "снег", 75: "сильный снег", 77: "снежная крупа", 80: "ливень", 81: "ливень",
    82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад", 95: "гроза",
    96: "гроза с градом", 99: "гроза с градом",
}
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
STORM_CODES = {95, 96, 99}


async def _get_json(url: str) -> Any:
    import aiohttp

    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                data = await response.json(content_type=None)
    except Exception as exc:  # noqa: BLE001 - причина уходит человеку текстом
        raise WeatherError("сервис погоды не отвечает — проверь интернет") from exc
    if isinstance(data, dict) and data.get("error"):
        raise WeatherError(f"сервис погоды ответил ошибкой: {data.get('reason', '')}")
    return data


async def find_place(name: str, fetch=_get_json) -> Place:
    data = await fetch(GEOCODE_URL.format(name=quote(name.strip())))
    results = (data or {}).get("results") or []
    if not results:
        raise WeatherError(f"не нашёл город «{name.strip()}»")
    best = results[0]
    title = best.get("name") or name
    region = best.get("admin1")
    if region and region != title:
        title = f"{title}, {region}"
    return Place(title, float(best["latitude"]), float(best["longitude"]))


async def forecast(lat: float, lon: float, fetch=_get_json) -> Forecast:
    data = await fetch(FORECAST_URL.format(lat=lat, lon=lon))
    try:
        current = data["current"]
        hourly = data["hourly"]
        hours = tuple(
            Hour(
                at=dt.datetime.fromisoformat(stamp),
                temp=float(hourly["temperature_2m"][index]),
                feels=float(hourly["apparent_temperature"][index]),
                rain_chance=int(hourly["precipitation_probability"][index] or 0),
                code=int(hourly["weather_code"][index] or 0),
                wind=float(hourly["wind_speed_10m"][index] or 0),
            )
            for index, stamp in enumerate(hourly["time"])
        )
        return Forecast(
            temp=float(current["temperature_2m"]),
            feels=float(current["apparent_temperature"]),
            precipitation=float(current.get("precipitation") or 0),
            code=int(current.get("weather_code") or 0),
            wind=float(current.get("wind_speed_10m") or 0),
            hours=hours,
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise WeatherError("сервис погоды прислал непонятный ответ") from exc


# ------------------------------------------------------------------ одежда

#: (ощущается не ниже, что надеть). Сверху вниз — от жары к морозу.
LAYERS: tuple[tuple[float, str], ...] = (
    (25, "лёгкий хлопковый боди или футболка, панама; гулять в тени, взять воду. "
         "С 11 до 16 в жару лучше переждать дома"),
    (20, "футболка или боди с коротким рукавом, лёгкие штаны, панама, тонкие носочки"),
    (15, "боди с длинным рукавом, штаны, лёгкая кофта, тонкая шапочка или панама"),
    (10, "боди, кофта и штаны (или трикотажный комбинезон), ветровка, тонкая шапка"),
    (5, "боди, флисовый слой, демисезонный комбинезон, шапка, ботиночки"),
    (0, "термобельё, флис, тёплый демисезонный комбинезон, тёплая шапка, варежки"),
    (-10, "термобельё, флис, зимний комбинезон, тёплая шапка и манишка, варежки, "
          "тёплые ботинки; в коляске — ещё плед или конверт"),
    (-15, "всё зимнее и сверху конверт или одеяло; лицо прикрыть от ветра кремом "
          "от мороза, гулять 30–40 минут"),
)
#: Холоднее этого малышу до года лучше не гулять или выйти совсем ненадолго.
TOO_COLD = -15


def clothes(feels: float) -> str:
    if feels < TOO_COLD:
        return (
            "слишком холодно для долгой прогулки: в таком возрасте лучше остаться дома "
            "или выйти на 15–20 минут, одевшись по-зимнему и с конвертом"
        )
    for threshold, text in LAYERS:
        if feels >= threshold:
            return text
    return LAYERS[-1][1]


def extras(forecast_: Forecast) -> list[str]:
    tips: list[str] = []
    if forecast_.code in STORM_CODES:
        tips.append("⛈ гроза — прогулку лучше перенести")
    elif forecast_.code in RAIN_CODES or forecast_.precipitation > 0.2:
        tips.append("☔ дождь — дождевик на коляску, непромокаемый верхний слой")
    elif forecast_.code in SNOW_CODES:
        tips.append("❄️ снег — непромокаемый комбинезон и варежки, запасные — с собой")
    if forecast_.wind >= 8:
        tips.append(f"💨 сильный ветер ({forecast_.wind:.0f} м/с) — закрыть коляску от ветра")
    elif forecast_.wind >= 5:
        tips.append(f"🍃 ветер {forecast_.wind:.0f} м/с — ощущается холоднее, шапку потеплее")
    return tips


def best_window(hours: Sequence[Hour], now: dt.datetime, length: int = 2) -> Optional[tuple[dt.datetime, float]]:
    """Лучшие два часа для прогулки сегодня с 9 до 20: без осадков и ближе к +18."""
    def badness(hour: Hour) -> float:
        score = abs(hour.feels - 18)
        score += hour.rain_chance / 10
        if hour.code in RAIN_CODES | STORM_CODES:
            score += 15
        score += max(0, hour.wind - 5) * 2
        return score

    candidates = [hour for hour in hours if 9 <= hour.at.hour <= 20 - length and hour.at >= now - dt.timedelta(minutes=30)]
    best = None
    for index, hour in enumerate(candidates):
        block = candidates[index : index + length]
        if len(block) < length or (block[-1].at - block[0].at).seconds != 3600 * (length - 1):
            continue
        score = sum(badness(item) for item in block)
        if best is None or score < best[1]:
            best = (hour.at, score, sum(item.feels for item in block) / length)
    if best is None:
        return None
    return best[0], best[2]


def _signed(value: float) -> str:
    rounded = round(value)
    return f"+{rounded}" if rounded > 0 else f"{rounded}" if rounded < 0 else "0"


def render(place: str, forecast_: Forecast, now: dt.datetime, name: str = "") -> str:
    sky = WEATHER_CODES.get(forecast_.code, "")
    who = f" для {name}" if name else ""
    lines = [
        f"🌤 <b>{place}</b>: {_signed(forecast_.temp)}°, ощущается как "
        f"{_signed(forecast_.feels)}°" + (f", {sky}" if sky else ""),
        "",
        f"👕 <b>Что надеть{who}:</b> {clothes(forecast_.feels)}.",
    ]
    tips = extras(forecast_)
    if tips:
        lines.append("")
        lines.extend(tips)

    window = best_window(forecast_.hours, now)
    if window is not None:
        start, feels = window
        lines.append("")
        lines.append(
            f"🕑 Лучшее время для прогулки: <b>{start:%H:%M}–{start + dt.timedelta(hours=2):%H:%M}</b>"
            f" (ощущается {_signed(feels)}°)"
        )
    lines.append("")
    lines.append(
        "<i>В коляске — на слой теплее, чем взрослому; если ходит сам — как взрослому. "
        "Проверка: шея и спинка тёплые и сухие — в самый раз.</i>"
    )
    return "\n".join(lines)
