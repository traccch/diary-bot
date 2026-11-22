"""Раздел «Малыш»: сон, нормы, замеры ВОЗ, погода и доступ для жены."""

from __future__ import annotations

import datetime as dt
import re
import unittest
from unittest import mock

from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Chat, Location, Message, Update, User

from bot.baby import growth, norms, sleep, weather
from bot.baby.charts import day_pieces
from bot.middlewares import AccessMiddleware

from .test_handlers import USER_ID, BotTestCase

NOW = dt.datetime(2026, 11, 22, 15, 40)
BIRTH = dt.date(2025, 12, 22)
WIFE = 888


class SleepParseTest(unittest.TestCase):
    def test_now_and_with_time(self):
        self.assertEqual(sleep.parse("уснул", NOW), sleep.Command(sleep.START, start=NOW))
        self.assertEqual(sleep.parse("Уснула в 13:20", NOW).start, NOW.replace(hour=13, minute=20))
        self.assertEqual(sleep.parse("проснулся 7.05", NOW).end, NOW.replace(hour=7, minute=5))

    def test_time_in_future_means_yesterday(self):
        late = dt.datetime(2026, 11, 22, 0, 30)
        self.assertEqual(sleep.parse("уснул 21:10", late).start, dt.datetime(2026, 11, 21, 21, 10))

    def test_range_over_midnight(self):
        command = sleep.parse("сон 21:30-6:40", dt.datetime(2026, 11, 22, 8, 0))
        self.assertEqual(command.start, dt.datetime(2026, 11, 21, 21, 30))
        self.assertEqual(command.end, dt.datetime(2026, 11, 22, 6, 40))

    def test_not_about_sleep(self):
        for text in ("кофе 300", "уснул 25:00", "сон 13:20", "120/80"):
            self.assertIsNone(sleep.parse(text, NOW), text)


class SleepSummaryTest(unittest.TestCase):
    def test_night_naps_and_24h(self):
        sleeps = [
            sleep.Sleep(1, dt.datetime(2026, 11, 21, 20, 0), dt.datetime(2026, 11, 22, 7, 0)),
            sleep.Sleep(2, dt.datetime(2026, 11, 22, 10, 0), dt.datetime(2026, 11, 22, 11, 30)),
            sleep.Sleep(3, dt.datetime(2026, 11, 22, 14, 40), None),
        ]
        summary = sleep.summarize(sleeps, NOW)
        self.assertEqual(summary.night.id, 1)
        self.assertEqual([nap.id for nap in summary.naps], [2, 3])
        self.assertEqual(summary.nap_time, dt.timedelta(hours=2, minutes=30))
        # за 24 часа: с 20:00 вчера ночь целиком, плюс дневные
        self.assertEqual(summary.last_24h, dt.timedelta(hours=11 + 2.5))

    def test_long_day_sleep_is_night(self):
        self.assertTrue(sleep.Sleep(1, NOW.replace(hour=12), NOW.replace(hour=18)).is_night(NOW))
        self.assertFalse(sleep.Sleep(1, NOW.replace(hour=12), NOW.replace(hour=13)).is_night(NOW))

    def test_pieces_split_at_midnight(self):
        night = sleep.Sleep(1, dt.datetime(2026, 11, 21, 20, 0), dt.datetime(2026, 11, 22, 7, 0))
        self.assertEqual(day_pieces([night], dt.date(2026, 11, 21), NOW), [(20.0, 24.0, True)])
        self.assertEqual(day_pieces([night], dt.date(2026, 11, 22), NOW), [(0.0, 7.0, True)])


class NormsTest(unittest.TestCase):
    def test_age(self):
        self.assertEqual(norms.age_months(BIRTH, NOW.date()), 11)
        self.assertEqual(norms.age_months(BIRTH, dt.date(2026, 11, 21)), 10)
        self.assertEqual(norms.age_text(BIRTH, NOW.date()), "11 мес.")

    def test_eleven_months(self):
        norm = norms.norm_for(11)
        self.assertEqual(norm.naps, (2, 2))
        self.assertEqual(norm.wake_hours, (3.0, 4.0))
        self.assertEqual(norm.total_hours, (12, 16))

    def test_verdict(self):
        self.assertEqual(norms.verdict(13, (12, 16)), "в норме")
        self.assertEqual(norms.verdict(9, (12, 16)), "маловато")


class GrowthTest(unittest.TestCase):
    def test_who_medians(self):
        day = (NOW.date() - BIRTH).days
        self.assertAlmostEqual(growth.zscore(growth.WEIGHT, "m", day, 9.42), 0, delta=0.05)
        self.assertAlmostEqual(growth.percentile(0), 50)
        self.assertLess(growth.zscore(growth.WEIGHT, "m", day, 7.0), -2)
        self.assertGreater(growth.median(growth.LENGTH, "m", day), growth.median(growth.LENGTH, "f", day))

    def test_parse(self):
        entry = growth.parse("рост 76 вес 9,4 голова 45 15.11", NOW.date())
        self.assertEqual(entry.on, dt.date(2026, 11, 15))
        self.assertEqual(entry.values, {"length": 76.0, "weight": 9.4, "head": 45.0})
        self.assertEqual(growth.parse("вес 9400 г", NOW.date()).values, {"weight": 9.4})
        self.assertIsNone(growth.parse("вес 300", NOW.date()))
        self.assertIsNone(growth.parse("кофе 300", NOW.date()))

    def test_describe(self):
        text = growth.describe(growth.WEIGHT, "m", BIRTH, NOW.date(), 9.4)
        self.assertIn("перцентиль", text)
        self.assertIn("в пределах нормы", text)


def _fake_forecast(feels: float, code: int = 3, wind: float = 2.0):
    hours = [f"2026-11-22T{hour:02d}:00" for hour in range(24)]

    async def fetch(url: str):
        if "geocoding" in url:
            return {"results": [{"name": "Казань", "admin1": "Татарстан", "latitude": 55.79, "longitude": 49.12}]}
        return {
            "current": {
                "temperature_2m": feels + 2, "apparent_temperature": feels,
                "precipitation": 0, "weather_code": code, "wind_speed_10m": wind,
            },
            "hourly": {
                "time": hours,
                "temperature_2m": [feels + 2] * 24,
                "apparent_temperature": [feels - 3 + (2 if 12 <= h <= 15 else 0) for h in range(24)],
                "precipitation_probability": [0] * 24,
                "weather_code": [code] * 24,
                "wind_speed_10m": [wind] * 24,
            },
        }

    return fetch


class WeatherTest(unittest.IsolatedAsyncioTestCase):
    def test_clothes_by_feels(self):
        self.assertIn("зимний", weather.clothes(-8))
        self.assertIn("панама", weather.clothes(22))
        self.assertIn("дома", weather.clothes(-20))

    async def test_forecast_and_render(self):
        found = await weather.forecast(55.0, 49.0, fetch=_fake_forecast(-6, code=71, wind=9))
        text = weather.render("Казань", found, NOW.replace(hour=9), "Миши")
        self.assertIn("Что надеть для Миши", text)
        self.assertIn("зимний комбинезон", text)
        self.assertIn("снег", text)
        self.assertIn("ветер", text)
        self.assertIn("Лучшее время для прогулки", text)

    async def test_place(self):
        place = await weather.find_place("Казань", fetch=_fake_forecast(0))
        self.assertEqual(place.name, "Казань, Татарстан")

    async def test_service_error(self):
        async def broken(url):
            raise weather.WeatherError("сервис погоды не отвечает")

        with self.assertRaises(weather.WeatherError):
            await weather.forecast(1, 2, fetch=broken)


class BabyBotTest(BotTestCase):
    """Сквозные сценарии через бота: хозяин заводит малыша и зовёт жену."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        key = StorageKey(bot_id=self.bot.id, chat_id=WIFE, user_id=WIFE)
        await self.dp.storage.set_state(key, None)
        await self.dp.storage.set_data(key, {})

    # ---------------------------------------------------- помощники

    def _wife_message(self, text: str = "", **extra) -> Message:
        return Message(
            message_id=1,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=WIFE, type="private"),
            from_user=User(id=WIFE, is_bot=False, first_name="Жена"),
            text=text or None,
            **extra,
        )

    async def wife_send(self, text: str) -> str:
        self._update_id += 1
        await self.dp.feed_update(self.bot, Update(update_id=self._update_id, message=self._wife_message(text)))
        return self.bot.texts[-1]

    async def wife_click(self, data: str) -> None:
        self._update_id += 1
        await self.dp.feed_update(
            self.bot,
            Update(
                update_id=self._update_id,
                callback_query=CallbackQuery(
                    id=str(self._update_id),
                    from_user=User(id=WIFE, is_bot=False, first_name="Жена"),
                    chat_instance="wife",
                    data=data,
                    message=self._wife_message("предыдущий ответ"),
                ),
            ),
        )

    async def set_up_child(self) -> None:
        await self.click("baby:setup")
        await self.send("Миша")
        await self.send(BIRTH.strftime("%d.%m.%Y"))
        await self.click("baby:sex:m")

    async def invite_wife(self) -> None:
        await self.click("baby:invite")
        code = re.search(r"/start (fam_\S+?)</code>", self.bot.texts[-1]).group(1)
        await self.wife_send(f"/start {code}")

    # ---------------------------------------------------- сценарии

    async def test_setup_and_sleep_buttons(self):
        await self.send("/baby")
        self.assertIn("заведём малыша", self.bot.texts[-1])
        await self.set_up_child()
        child, role = await self.db.baby_of(USER_ID)
        self.assertEqual((child.name, child.sex, child.birth, role), ("Миша", "m", BIRTH, "owner"))

        await self.click("baby:sleep")
        self.assertIn("Уснул", self.bot.texts[-1])
        self.assertIn("🌞 Проснулся", self.bot.last_buttons)
        await self.click("baby:sleep")
        self.assertIn("Уже спит", self.bot.texts[-1])
        await self.click("baby:wake")
        self.assertIn("Проснулся", self.bot.texts[-1])
        self.assertIsNone(await self.db.baby_open_sleep(child.id))

        await self.click("baby:day")
        self.assertIn("За последние 24 часа", self.bot.texts[-1])
        await self.click("baby:week")
        self.assertTrue(self.bot.photos())

        await self.click("baby:undo")
        self.assertIn("Удалил", self.bot.texts[-1])

    async def test_owner_words_and_own_diary_do_not_clash(self):
        await self.set_up_child()
        child, _ = await self.db.baby_of(USER_ID)
        await self.send("уснул 13:20")
        self.assertIsNotNone(await self.db.baby_open_sleep(child.id))
        await self.send("проснулся")
        self.assertIsNone(await self.db.baby_open_sleep(child.id))

        # «сон 23:21-7:01» у хозяина — его собственный сон в дневнике
        before = len(await self.db.baby_sleeps(child.id, dt.datetime(2000, 1, 1)))
        await self.send("сон 23:21-7:01")
        self.assertEqual(len(await self.db.baby_sleeps(child.id, dt.datetime(2000, 1, 1))), before)

    async def test_wife_sees_only_the_baby(self):
        await self.set_up_child()
        await self.invite_wife()
        self.assertIn("дневник малыша", " ".join(self.bot.texts[-3:]))
        self.assertEqual(await self.db.baby_role(WIFE), "family")
        self.assertEqual(await self.db.list_reminders(WIFE), [], "чужих напоминаний у неё нет")

        # английский, давление и главное меню ей недоступны — вместо них малыш
        for text in ("/eng", "/start", "/help", "/engstats"):
            answer = await self.wife_send(text)
            self.assertIn("Миша", answer, text)
        await self.wife_click("eng:more")
        self.assertIn("Миша", self.bot.texts[-1])
        self.assertFalse(await self.db.eng_progress(WIFE))

        # её текст — про малыша, а не трата или давление
        await self.wife_send("уснул 13:20")
        child, _ = await self.db.baby_of(WIFE)
        self.assertIsNotNone(await self.db.baby_open_sleep(child.id))
        await self.wife_send("проснулся 14:50")
        answer = await self.wife_send("сон 10:00-11:15")
        self.assertIn("Записал сон", answer)
        answer = await self.wife_send("рост 76 вес 9,4 голова 45")
        self.assertIn("перцентиль", answer)
        self.assertEqual(len(await self.db.baby_growth(child.id)), 3)
        self.assertIn("Не понял", await self.wife_send("кофе 300"))
        self.assertFalse(await self.db.last_transactions(WIFE))

        # хозяин видит то же, что записала жена
        await self.click("baby:growth")
        self.assertIn("9,4", self.bot.texts[-1])

    async def test_invite_link_works_once(self):
        await self.set_up_child()
        await self.click("baby:invite")
        code = re.search(r"/start (fam_\S+?)</code>", self.bot.texts[-1]).group(1)
        self.assertTrue(await AccessMiddleware.invited(self._wife_message(f"/start {code}"), self.db))
        await self.wife_send(f"/start {code}")
        self.assertFalse(await AccessMiddleware.invited(self._wife_message(f"/start {code}"), self.db))
        self.assertTrue(await AccessMiddleware().allowed(WIFE, self.db))
        self.assertFalse(await AccessMiddleware().allowed(999, self.db))

    async def test_owner_can_remove_access(self):
        await self.set_up_child()
        await self.invite_wife()
        await self.click(f"baby:kick:{WIFE}")
        self.assertIn("Доступ убран", self.bot.texts[-1])
        self.assertIsNone(await self.db.baby_role(WIFE))

    async def test_city_and_weather(self):
        from bot.baby import handlers as baby

        await self.set_up_child()
        await self.invite_wife()
        fake = _fake_forecast(-6)
        with mock.patch("bot.baby.weather._get_json", fake):
            answer = await self.wife_send("город Казань")
        self.assertIn("Казань", answer)
        child, _ = await self.db.baby_of(WIFE)
        self.assertIsNotNone(child.lat)
        # утренний прогноз — ей, раз город указала она
        self.assertTrue(await self.db.list_reminders(WIFE, "baby"))

        text = await baby.weather_text(child, NOW, fetch=fake)
        self.assertIn("Что надеть для Миши", text)

    async def test_location_sets_place(self):
        await self.set_up_child()
        self._update_id += 1
        message = Message(
            message_id=1, date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=555, type="private"),
            from_user=User(id=USER_ID, is_bot=False, first_name="Тест"),
            location=Location(latitude=55.7, longitude=49.1),
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._update_id, message=message))
        child, _ = await self.db.baby_of(USER_ID)
        self.assertAlmostEqual(child.lat, 55.7)

    async def test_home_has_baby_button(self):
        await self.send("/help")
        self.assertIn("👶 Малыш", self.bot.last_buttons)

    async def test_wife_gets_morning_weather_if_city_known(self):
        await self.set_up_child()
        child, _ = await self.db.baby_of(USER_ID)
        await self.db.baby_update(child.id, city="Казань", lat=55.7, lon=49.1)
        await self.invite_wife()
        reminders = await self.db.list_reminders(WIFE)
        self.assertEqual([item.topic for item in reminders], ["baby"])
