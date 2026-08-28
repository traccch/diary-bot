"""Смоук-тесты хендлеров: апдейты прогоняются через диспетчер с фейковым ботом.

Проверяем роутинг, внедрение зависимостей и ответы, не ходя в сеть.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    EditMessageText,
    SendDocument,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.methods.base import TelegramType
from aiogram.types import CallbackQuery, Chat, Message, Update, User, Voice

from bot.db import Database
from bot.handlers import build_router
from bot.middlewares import UserMiddleware
from bot.updater import UpdateResult, UpdateStatus
from bot.voice import VoiceConfig, build_transcriber

CHAT_ID = 555
USER_ID = 777

ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a"}
TAG_RE = re.compile(r"</?([a-zA-Z]+)[^>]*>")


class RecordingBot(Bot):
    """Бот, который вместо HTTP-запроса складывает вызовы в список."""

    def __init__(self) -> None:
        super().__init__(
            token="42:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN",
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.calls: list[TelegramMethod[Any]] = []
        #: Что «скачается», когда хендлер попросит содержимое документа
        self.file_content = b""

    async def __call__(self, method: TelegramMethod[TelegramType], request_timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, SendDocument, SendPhoto)):
            # .as_(self) — чтобы у ответа можно было вызвать edit_text,
            # как у настоящего сообщения от Telegram
            return Message(
                message_id=len(self.calls),
                date=dt.datetime.now(dt.timezone.utc),
                chat=Chat(id=CHAT_ID, type="private"),
            ).as_(self)
        return True

    async def download(self, file, destination=None, **kwargs):
        """Вместо похода в Telegram отдаёт заранее подложенное содержимое."""
        if destination is not None:
            destination.write(self.file_content)
            return destination
        from io import BytesIO

        return BytesIO(self.file_content)

    @property
    def texts(self) -> list[str]:
        return [
            call.text
            for call in self.calls
            if isinstance(call, SendMessage) and call.text is not None
        ]

    @property
    def edits(self) -> list[str]:
        """Тексты правок сообщений — кнопки чаще редактируют, а не шлют новое."""
        return [
            call.text
            for call in self.calls
            if isinstance(call, EditMessageText) and call.text is not None
        ]

    @property
    def last_buttons(self) -> list[str]:
        """Подписи кнопок последнего сообщения — по ним тест выбирает ответ."""
        for call in reversed(self.calls):
            markup = getattr(call, "reply_markup", None)
            if markup is not None and getattr(markup, "inline_keyboard", None):
                return [
                    button.text for row in markup.inline_keyboard for button in row
                ]
        return []

    def documents(self) -> list[SendDocument]:
        return [call for call in self.calls if isinstance(call, SendDocument)]

    def photos(self) -> list[SendPhoto]:
        return [call for call in self.calls if isinstance(call, SendPhoto)]


class FakeUpdater:
    """Обновлятель без git: отдаёт заданный ответ и запоминает вызовы."""

    def __init__(self) -> None:
        self.status = UpdateStatus(
            branch="main", local="0000000", remote="0000000", behind=0
        )
        self.result = UpdateResult(True, "Обновился. Перезапускаюсь.", restart=True)
        self.applied = 0
        #: О каких шагах отчитаться по ходу дела (зависимости обычно не трогаем)
        self.steps = ("pull", "tests", "restart")

    def is_git_repo(self) -> bool:
        return True

    async def check(self) -> UpdateStatus:
        return self.status

    async def apply(self, run_tests: bool = True, progress=None) -> UpdateResult:
        self.applied += 1
        for step in self.steps if progress is not None else ():
            await progress(step)
        return self.result


_dispatcher: Dispatcher | None = None


def get_dispatcher() -> Dispatcher:
    """Роутеры aiogram — объекты уровня модуля, подключить их можно лишь однажды,
    поэтому диспетчер собирается один раз на прогон, а база подменяется в setUp."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher(storage=MemoryStorage())
        middleware = UserMiddleware()
        _dispatcher.message.middleware(middleware)
        _dispatcher.callback_query.middleware(middleware)
        _dispatcher.include_router(build_router())
    return _dispatcher


def make_message(text: str, message_id: int = 1) -> Message:
    return Message(
        message_id=message_id,
        date=dt.datetime.now(dt.timezone.utc),
        chat=Chat(id=CHAT_ID, type="private"),
        from_user=User(id=USER_ID, is_bot=False, first_name="Тест"),
        text=text,
    )


class BotTestCase(unittest.IsolatedAsyncioTestCase):
    """Общая обвязка: своя база, общий диспетчер, бот-заглушка."""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "test.db"), "Europe/Moscow")
        await self.db.connect()

        self.dp = get_dispatcher()
        self.dp["db"] = self.db
        self.updater = FakeUpdater()
        self.dp["updater"] = self.updater
        self.dp["owner_id"] = None
        self.restart_event = asyncio.Event()
        self.dp["restart_event"] = self.restart_event
        self.dp["transcriber"] = build_transcriber(VoiceConfig())

        self.bot = RecordingBot()
        self._update_id = 0

        # диспетчер общий на весь прогон — состояние FSM чистим руками
        key = StorageKey(bot_id=self.bot.id, chat_id=CHAT_ID, user_id=USER_ID)
        await self.dp.storage.set_state(key, None)
        await self.dp.storage.set_data(key, {})

    async def asyncTearDown(self):
        await self.bot.session.close()
        await self.db.close()
        self._tmp.cleanup()

    async def send(self, text: str) -> str:
        self._update_id += 1
        await self.dp.feed_update(
            self.bot, Update(update_id=self._update_id, message=make_message(text))
        )
        return self.bot.texts[-1]

    async def send_voice(self, duration: int = 4) -> None:
        self._update_id += 1
        message = make_message("")
        message = message.model_copy(
            update={
                "text": None,
                "voice": Voice(
                    file_id="voice-1",
                    file_unique_id="u1",
                    duration=duration,
                ),
            }
        )
        await self.dp.feed_update(
            self.bot, Update(update_id=self._update_id, message=message)
        )

    async def send_document(self, content: bytes, name: str = "operations.json") -> None:
        from aiogram.types import Document

        self.bot.file_content = content
        self._update_id += 1
        message = make_message("").model_copy(
            update={
                "text": None,
                "document": Document(
                    file_id="doc-1",
                    file_unique_id="u1",
                    file_name=name,
                    file_size=len(content),
                ),
            }
        )
        await self.dp.feed_update(
            self.bot, Update(update_id=self._update_id, message=message)
        )

    async def click(self, data: str) -> None:
        self._update_id += 1
        await self.dp.feed_update(
            self.bot,
            Update(
                update_id=self._update_id,
                callback_query=CallbackQuery(
                    id=str(self._update_id),
                    from_user=User(id=USER_ID, is_bot=False, first_name="Тест"),
                    chat_instance="test",
                    data=data,
                    message=make_message("предыдущий ответ", message_id=100),
                ),
            ),
        )


class HandlersTest(BotTestCase):
    # ------------------------------------------------------------- базовое

    async def test_start_and_help(self):
        self.assertIn("три раздела", (await self.send("/start")).lower())
        self.assertIn("Что сделать", await self.send("/help"))
        self.assertIn("/stats", await self.send("/commands"))
        self.assertIn("ESC", await self.send("/about"))

    async def test_add_measurement(self):
        answer = await self.send("120/80 68")
        self.assertIn("120/80", answer)
        self.assertIn("68", answer)

        stored = await self.db.last_measurements(USER_ID)
        self.assertEqual(len(stored), 1)
        self.assertEqual((stored[0].systolic, stored[0].diastolic, stored[0].pulse), (120, 80, 68))

    async def test_first_measurement_suggests_reminders(self):
        self.assertIn("/remind", await self.send("120/80"))
        self.assertNotIn("/remind", await self.send("125/82"))

    async def test_measurement_with_note_and_time(self):
        await self.send("135/85 72 вчера 21:30 после прогулки")
        stored = (await self.db.last_measurements(USER_ID))[0]
        self.assertEqual(stored.note, "после прогулки")
        self.assertEqual(stored.measured_at.hour, 21)

    async def test_crisis_gets_a_warning(self):
        self.assertIn("103", await self.send("190/115"))

    async def test_garbage_gets_a_hint(self):
        self.assertIn("120/80", await self.send("привет"))
        self.assertIn("120/80", await self.send("12080"))
        self.assertEqual(await self.db.count_measurements(USER_ID), 0)
        self.assertEqual(await self.db.count_transactions(USER_ID), 0)

    async def test_swapped_numbers_explained(self):
        self.assertIn("Верхнее должно быть больше", await self.send("80/120"))
        self.assertEqual(await self.db.count_measurements(USER_ID), 0)

    async def test_week_context_appears(self):
        for _ in range(3):
            await self.send("130/85 70")
        self.assertIn("За 7 дней", self.bot.texts[-1])

    # ------------------------------------------------------------- разделы

    async def test_menu_switches_section(self):
        self.assertIn("Давление", await self.send("/menu"))
        await self.click("go:money")
        self.assertEqual((await self.db.ensure_user(USER_ID)).section, "money")
        self.assertIn("Деньги", self.bot.edits[-1])

    async def test_money_entry_and_stats(self):
        await self.click("go:money")
        answer = await self.send("кофе 300")
        self.assertIn("300", answer)
        self.assertIn("Кафе", answer)
        self.assertEqual(await self.db.count_transactions(USER_ID), 1)

        report = await self.send("/stats")
        self.assertIn("Расходы", report)
        self.assertIn("Куда ушло", report)

    async def test_income_through_plus(self):
        await self.click("go:money")
        answer = await self.send("+90000 зарплата")
        self.assertIn("Доход", answer)
        self.assertIn("Зарплата", answer)
        self.assertIn("Остаток", await self.send("/balance"))

    async def test_pressure_is_recognised_from_money_section(self):
        await self.click("go:money")
        answer = await self.send("120/80 68")
        self.assertIn("120/80", answer)
        self.assertEqual(await self.db.count_measurements(USER_ID), 1)
        self.assertEqual(await self.db.count_transactions(USER_ID), 0)

    async def test_expense_is_recognised_from_pressure_section(self):
        answer = await self.send("такси 450")
        self.assertIn("450", answer)
        self.assertEqual(await self.db.count_transactions(USER_ID), 1)
        self.assertEqual(await self.db.count_measurements(USER_ID), 0)

    async def test_stats_follows_the_section(self):
        await self.send("120/80 68")
        await self.send("кофе 300")

        self.assertIn("Давление", await self.send("/stats"))
        await self.click("go:money")
        self.assertIn("Деньги", await self.send("/stats"))

    async def test_money_commands_are_absent_in_pressure(self):
        # /limit принадлежит деньгам и работает всегда — он не общий
        self.assertIn("лимит", (await self.send("/limit")).lower())

    async def test_last_and_undo_follow_the_section(self):
        await self.send("120/80")
        await self.click("go:money")
        await self.send("кофе 300")

        self.assertIn("Кафе", await self.send("/last"))
        self.assertIn("Удалил", await self.send("/undo"))
        self.assertEqual(await self.db.count_transactions(USER_ID), 0)
        self.assertEqual(await self.db.count_measurements(USER_ID), 1)

    async def test_money_categories_and_limits(self):
        await self.click("go:money")
        self.assertIn("Расходы", await self.send("/cats"))
        self.assertIn("Пицца", await self.send("/addcat 🍕 Пицца, додо"))
        await self.send("додо 700")
        stored = (await self.db.last_transactions(USER_ID))[0]
        self.assertEqual(stored.category_name, "Пицца")

        self.assertIn("Установил", await self.send("/limit 1000"))
        self.assertIn("🔴", await self.send("шаурма 500"))
        self.assertIn("Всего за месяц", await self.send("/limits"))

    async def test_money_export_is_csv(self):
        await self.click("go:money")
        await self.send("кофе 300")
        await self.send("/export")
        payload = self.bot.documents()[-1].document.data.decode("utf-8-sig")
        self.assertIn("кофе", payload)
        self.assertIn("расход", payload)

    async def test_voice_without_recogniser_explains_itself(self):
        await self.send_voice()
        self.assertIn("Голосовые", self.bot.texts[-1])

    # ------------------------------------------------- показатели здоровья

    async def test_measurement_with_sleep_in_one_line(self):
        answer = await self.send("120/80 68 сон 23:21-7:01")
        self.assertIn("120/80", answer)
        self.assertIn("7 ч 40 мин", answer)
        self.assertIn("23:21", answer)

        stored = await self.db.get_metric(USER_ID, "sleep", dt.date.today())
        self.assertEqual(stored.value, 460)

    async def test_metrics_without_pressure(self):
        answer = await self.send("шаги 8200 пульс покоя 58")
        self.assertIn("Записал", answer)
        self.assertIn("8 200", answer)
        self.assertIn("58", answer)
        self.assertEqual(await self.db.count_measurements(USER_ID), 0)
        self.assertEqual(await self.db.count_metrics(USER_ID), 2)

    async def test_weight_replaces_the_same_day(self):
        await self.send("вес 78,5")
        await self.send("вес 78,2")
        stored = await self.db.metrics_between(
            USER_ID, "weight", dt.date.today(), dt.date.today()
        )
        self.assertEqual([item.value for item in stored], [78.2])

    async def test_absurd_metric_is_explained(self):
        self.assertIn("опечатку", await self.send("сон 30 часов"))
        self.assertEqual(await self.db.count_metrics(USER_ID), 0)

    async def test_health_block_appears_in_stats(self):
        for offset in range(12):
            date = dt.date.today() - dt.timedelta(days=offset)
            await self.db.set_metric(USER_ID, "sleep", date, 330 if offset % 2 else 480)
            await self.db.add_measurement(
                USER_ID,
                145 if offset % 2 else 128,
                92 if offset % 2 else 82,
                70,
                dt.datetime.combine(date, dt.time(8, 0)),
            )
        report = await self.send("/stats")
        self.assertIn("Здоровье", report)
        self.assertIn("Сон", report)

    async def test_metric_chart(self):
        for offset in range(6):
            await self.db.set_metric(
                USER_ID, "steps", dt.date.today() - dt.timedelta(days=offset), 6000 + offset * 500
            )
        await self.click("chart:steps:month")
        self.assertEqual(len(self.bot.photos()), 1)
        self.assertIn("Шаги", self.bot.photos()[0].caption)

    async def test_metric_chart_without_data(self):
        await self.click("chart:weight:month")
        self.assertIn("меньше двух записей", self.bot.texts[-1])

    # ------------------------------------------------------------ /add и FSM

    async def test_guided_add(self):
        self.assertIn("тонометр", await self.send("/add"))
        self.assertIn("Пока не вижу", await self.send("не помню"))
        self.assertIn("128/84", await self.send("128/84 70"))
        self.assertEqual(await self.db.count_measurements(USER_ID), 1)

        # состояние сброшено — обычный текст снова просто подсказка
        self.assertIn("120/80", await self.send("привет"))

    async def test_cancel_leaves_the_flow(self):
        await self.send("/add")
        self.assertIn("Отменил", await self.send("/cancel"))
        self.assertIn("Нечего отменять", await self.send("/cancel"))

    async def test_comment_button_writes_note(self):
        await self.send("120/80")
        measurement = (await self.db.last_measurements(USER_ID))[0]
        await self.click(f"note:{measurement.id}")
        self.assertIn("комментарий", self.bot.texts[-1].lower())

        await self.send("после лекарства")
        self.assertEqual(
            (await self.db.get_measurement(USER_ID, measurement.id)).note, "после лекарства"
        )

    async def test_comment_can_be_skipped(self):
        await self.send("120/80")
        measurement = (await self.db.last_measurements(USER_ID))[0]
        await self.click(f"note:{measurement.id}")
        await self.click("note:skip")
        self.assertEqual((await self.db.get_measurement(USER_ID, measurement.id)).note, "")
        # состояние сброшено: следующий текст снова разбирается как измерение
        await self.send("125/82")
        self.assertEqual(await self.db.count_measurements(USER_ID), 2)

    # ------------------------------------------------------- список и удаление

    async def test_last_undo_and_delete(self):
        await self.send("120/80")
        await self.send("130/85")
        self.assertIn("130/85", await self.send("/last"))

        self.assertIn("Удалил", await self.send("/undo"))
        self.assertEqual(await self.db.count_measurements(USER_ID), 1)

        measurement = (await self.db.last_measurements(USER_ID))[0]
        await self.click(f"del:{measurement.id}")
        self.assertEqual(await self.db.count_measurements(USER_ID), 0)

        self.assertIn("Укажи номер", await self.send("/del"))
        self.assertIn("Такого измерения нет", await self.send("/del 999"))

    # ---------------------------------------------------------------- отчёты

    async def test_stats_and_periods(self):
        self.assertIn("пуст", await self.send("/stats"))
        for day in range(6):
            await self.send(f"{125 + day}/8{day % 6} 70")
        report = await self.send("/stats")
        self.assertIn("Среднее", report)
        for period in ("week", "month", "quarter", "all"):
            await self.click(f"stats:{period}")

    async def test_chart_needs_two_points(self):
        await self.send("120/80")
        await self.send("/chart")
        self.assertIn("хотя бы два", self.bot.texts[-1])

        await self.send("130/85 вчера")
        await self.send("/chart")
        self.assertEqual(len(self.bot.photos()), 1)

    # -------------------------------------------------------------- выгрузка

    async def test_export_menu_and_files(self):
        self.assertIn("пуст", await self.send("/export"))
        await self.send("120/80 65")
        await self.send("140/90 70 вчера")
        self.assertIn("PDF", await self.send("/export"))

        await self.click("exp:csv:all")
        csv = self.bot.documents()[-1].document.data.decode("utf-8-sig")
        self.assertIn("верхнее", csv)
        self.assertIn("140", csv)

        await self.click("exp:pdf:month")
        pdf = self.bot.documents()[-1]
        self.assertTrue(pdf.document.filename.endswith(".pdf"))
        self.assertTrue(pdf.document.data.startswith(b"%PDF"))

    # ----------------------------------------------------------- напоминания

    async def test_reminder_lifecycle(self):
        # умолчания проверяются в test_english, здесь важен сам цикл команд.
        # ensure_user до удаления: пользователя создаёт первое же сообщение,
        # и вместе с ним появляются напоминания по умолчанию.
        await self.db.ensure_user(USER_ID)
        await self.db.delete_all_reminders(USER_ID)
        self.assertIn("Напоминания", await self.send("/remind"))
        self.assertIn("08:00", await self.send("/remind 08:00"))
        self.assertIn("21:00", await self.send("/remind 21:00"))
        self.assertIn("уже есть", await self.send("/remind 8:00"))
        self.assertIn("Не понял время", await self.send("/remind скоро"))

        listing = await self.send("/reminders")
        self.assertIn("08:00", listing)
        self.assertIn("21:00", listing)

        await self.click("remdel:pressure:08:00")
        self.assertNotIn("08:00", self.bot.edits[-1])
        self.assertIn("21:00", self.bot.edits[-1])

        await self.click("remskip")
        self.assertIn("в любом случае", self.bot.edits[-1])

        self.assertIn("Выключил", await self.send("/remind off"))
        self.assertIn("Напоминаний нет", await self.send("/reminders"))

    async def test_reminder_buttons(self):
        await self.click("rem:write")
        self.assertIn("тонометр", self.bot.texts[-1])
        await self.send("118/76")
        self.assertEqual(await self.db.count_measurements(USER_ID), 1)

        await self.click("rem:snooze")  # тихо ставит отложенное напоминание

    # ---------------------------------------------------------- обновление

    async def test_version_when_up_to_date(self):
        self.assertIn("Последняя версия", await self.send("/version"))

    async def test_update_offers_a_button(self):
        self.updater.status = UpdateStatus(
            branch="main", local="aaaaaaa", remote="bbbbbbb", behind=2,
            messages=["Починил разбор сна", "Добавил график веса"],
        )
        answer = await self.send("/update")
        self.assertIn("Обновление до", answer)
        self.assertIn("Починил разбор сна", answer)

        await self.click("upd:apply")
        self.assertEqual(self.updater.applied, 1)
        self.assertIn("Перезапускаюсь", self.bot.edits[-1])
        self.assertTrue(self.restart_event.is_set())

    async def test_update_shows_what_it_is_doing(self):
        """Полторы минуты молчания неотличимы от зависшего бота."""
        await self.click("upd:apply")

        started = self.bot.texts[-1]
        self.assertIn("Обновляюсь", started)
        self.assertIn("Забираю новый код", started)

        # шаги отмечаются по ходу дела, а не одним махом в конце
        steps = "\n".join(self.bot.edits)
        self.assertIn("✅ Забираю новый код", steps)
        self.assertIn("⏳ Прогоняю тесты", steps)
        self.assertIn("⏭ Ставлю зависимости", steps)  # requirements не менялись

    async def test_progress_message_becomes_the_answer(self):
        """Итог заменяет собой индикатор — лишних сообщений в чате не остаётся."""
        before = len(self.bot.texts)
        await self.click("upd:apply")
        self.assertEqual(len(self.bot.texts), before + 1)
        self.assertIn("Перезапускаюсь", self.bot.edits[-1])

    async def test_failed_update_does_not_restart(self):
        self.updater.result = UpdateResult(False, "Не прошла тесты, вернул прежнюю.")
        await self.click("upd:apply")
        self.assertIn("вернул прежнюю", self.bot.edits[-1])
        self.assertFalse(self.restart_event.is_set())

    async def test_only_owner_can_update(self):
        self.dp["owner_id"] = 999_999
        self.assertIn("только для владельца", await self.send("/update"))
        await self.click("upd:apply")
        self.assertEqual(self.updater.applied, 0)
        self.assertFalse(self.restart_event.is_set())

    # ---------------------------------------------------------- настройки

    async def test_settings(self):
        self.assertIn("135/85", await self.send("/target"))
        self.assertIn("130/80", await self.send("/target 130/80"))
        self.assertIn("странно", await self.send("/target 300/20"))
        self.assertIn("два числа", await self.send("/target абв"))

        self.assertIn("Europe/Moscow", await self.send("/tz"))
        self.assertIn("Asia/Almaty", await self.send("/tz Asia/Almaty"))

    async def test_timezone_is_chosen_by_city(self):
        """Пояс IANA не помнит никто, город знают все."""
        await self.send("/tz")
        self.assertTrue(any("Красноярск" in text for text in self.bot.last_buttons))

        # материк в имени пояса — обычная ошибка, её надо прощать
        answer = await self.send("/tz Europe/Krasnoyarsk")
        self.assertIn("кажется, ты про это", answer)
        self.assertTrue(any("Krasnoyarsk" in text for text in self.bot.last_buttons))

        await self.click("tz:Asia/Krasnoyarsk")
        user = await self.db.ensure_user(USER_ID)
        self.assertEqual(user.tz, "Asia/Krasnoyarsk")

    async def test_timezone_by_russian_name(self):
        await self.send("/tz Новосибирск")
        self.assertTrue(any("Novosibirsk" in text for text in self.bot.last_buttons))

    async def test_unknown_timezone_explains_how(self):
        self.assertIn("Красноярск", await self.send("/tz абракадабра"))
        self.assertIn("Не знаю", await self.send("/tz Мордор"))

    async def test_all_answers_use_valid_html(self):
        await self.send("120/80 65")
        await self.send("140/90 70 вчера")
        for text in ("/start", "/help", "/about", "/stats", "/last", "/reminders",
                     "/remind 08:00", "/export", "/target"):
            await self.send(text)
        for text in self.bot.texts:
            for tag in TAG_RE.findall(text):
                self.assertIn(tag.lower(), ALLOWED_TAGS, f"недопустимый тег <{tag}> в: {text}")


if __name__ == "__main__":
    unittest.main()


class EnglishTest(BotTestCase):
    """Раздел английского: сессия карточек, квест, перевод слова, прогресс."""

    async def to_english(self) -> None:
        await self.click("go:english")

    async def answer_correctly(self) -> str:
        """Находит верную кнопку по тексту карточки и жмёт её."""
        from bot.english import content

        question = self.bot.texts[-1]
        buttons = self.bot.last_buttons
        for index, option in enumerate(buttons):
            card = next(
                (
                    item
                    for item in content.CARDS
                    if option in (item.ru, item.en) and (item.en in question or item.ru in question)
                ),
                None,
            )
            if card is not None and (
                (f"<b>{card.en}</b>" in question and option == card.ru)
                or (f"<b>{card.ru}</b>" in question and option == card.en)
            ):
                await self.click(f"eng:a:{index}")
                return self.bot.edits[-1]
        # вопрос с пропуском: верный вариант — слово из примера
        await self.click("eng:idk")
        return self.bot.edits[-1]

    async def test_menu_shows_progress(self):
        answer = await self.send("/eng")
        self.assertIn("Английский", answer)
        self.assertIn("Слов в работе", answer)

    async def test_session_asks_and_records(self):
        await self.send("/eng")
        await self.click("eng:more")

        self.assertIn("1/", self.bot.texts[-1])
        self.assertGreaterEqual(len(self.bot.last_buttons), 4)

        feedback = await self.answer_correctly()
        self.assertTrue(
            any(word in feedback for word in ("Верно", "Нет", "Ничего страшного"))
        )

        progress = await self.db.eng_progress(USER_ID)
        self.assertEqual(len(progress), 1, "ответ должен быть записан сразу")

        day = await self.db.eng_day(USER_ID, dt.date.today())
        self.assertEqual(day.answered, 1)

    async def test_session_walks_to_the_end(self):
        await self.send("/eng")
        await self.click("eng:more")

        for _ in range(30):
            if "Сессия закончена" in self.bot.texts[-1]:
                break
            if self.bot.last_buttons and "Дальше" in self.bot.last_buttons[0]:
                await self.click("eng:next")
            else:
                await self.click("eng:idk")

        self.assertIn("Сессия закончена", self.bot.texts[-1])
        self.assertIn("Серия", self.bot.texts[-1])

    async def test_wrong_answer_resets_the_box(self):
        from bot.english import srs

        await self.db.ensure_user(USER_ID)
        await self.db.eng_save_answer(
            USER_ID, "games:loot", 3, dt.date.today(), True, False
        )

        await self.send("/eng")
        await self.click("eng:more")
        # ответим наверняка неверно: берём вариант, который не совпал бы
        question = self.bot.texts[-1]
        buttons = self.bot.last_buttons
        wrong = next(
            index
            for index, text in enumerate(buttons)
            if text not in question and text != "🤷 Не знаю"
        )
        await self.click(f"eng:a:{wrong}")

        touched = [item for item in await self.db.eng_progress(USER_ID) if item.seen]
        self.assertTrue(touched)
        self.assertTrue(
            all(item.box < srs.LEARNED_BOX for item in touched if item.lapses)
        )

    async def test_dont_know_is_not_a_mistake(self):
        await self.send("/eng")
        await self.click("eng:more")
        await self.click("eng:idk")

        self.assertIn("Ничего страшного", self.bot.edits[-1])
        progress = await self.db.eng_progress(USER_ID)
        self.assertEqual(progress[0].lapses, 0, "«не знаю» не портит статистику")

    async def test_quest_can_be_finished(self):
        await self.send("/quest")
        self.assertIn("Слова, которые встретятся", self.bot.texts[-1])

        await self.click("eq:go:tavern")
        self.assertIn("Вопрос 1", self.bot.texts[-1])

        for _ in range(3):
            await self.click("eq:a:1")
            if "Квест" in self.bot.texts[-1]:
                break
            await self.click("eq:next")

        self.assertTrue(await self.db.eng_done_quests(USER_ID))

    async def test_word_lookup_in_english_section(self):
        await self.to_english()
        answer = await self.send("loot")
        self.assertIn("добыча", answer)

        await self.click("eng:add:games:loot")
        self.assertIsNotNone(await self.db.eng_progress_of(USER_ID, "games:loot"))

    async def test_unknown_word_is_explained(self):
        await self.to_english()
        self.assertIn("нет", await self.send("supercalifragilistic"))

    async def test_numbers_still_reach_their_sections(self):
        """Из английского раздела давление и траты всё равно записываются."""
        await self.to_english()
        await self.send("120/80 68")
        self.assertTrue(await self.db.last_measurements(USER_ID))

        await self.send("кофе 300")
        self.assertTrue(await self.db.last_transactions(USER_ID))

    async def test_progress_screen(self):
        await self.send("/engstats")
        answer = self.bot.texts[-1]
        self.assertIn("прогресс", answer.lower())
        self.assertIn("Квесты", answer)

    async def test_stats_command_works_in_section(self):
        await self.to_english()
        await self.send("/stats")
        self.assertIn("Английский", self.bot.texts[-1])
