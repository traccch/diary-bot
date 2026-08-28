"""Кнопочное меню: всё нужное — нажатием, без единой команды."""

from __future__ import annotations

from bot import sections

from .test_handlers import USER_ID, BotTestCase


class HubTest(BotTestCase):
    async def open(self, data: str) -> list[str]:
        await self.click(data)
        return self.bot.last_buttons

    async def test_help_is_a_menu_not_a_wall_of_text(self):
        answer = await self.send("/help")
        self.assertIn("Что сделать", answer)
        self.assertIn("🩺 Давление", self.bot.last_buttons)
        self.assertIn("⚙️ Настройки", self.bot.last_buttons)

    async def test_full_list_of_commands_is_still_there(self):
        self.assertIn("/stats", await self.send("/commands"))

    async def test_screens_open_and_go_back(self):
        self.assertIn("✍️ Записать измерение", await self.open("do:bp"))
        self.assertIn("← Назад", self.bot.last_buttons)

        self.assertIn("🩺 Давление", await self.open("do:home"))

    # ------------------------------------------------------------ давление

    async def test_write_measurement_by_button(self):
        await self.click("do:bp:add")
        self.assertIn("тонометр", self.bot.texts[-1])

        await self.send("120/80 68")
        stored = await self.db.last_measurements(USER_ID)
        self.assertEqual((stored[0].systolic, stored[0].pulse), (120, 68))

    async def test_pressure_actions(self):
        await self.send("120/80 68")
        await self.click("do:bp:last")
        self.assertIn("120/80", self.bot.texts[-1])

        await self.click("do:bp:stats")
        self.assertTrue(self.bot.texts[-1])

    # -------------------------------------------------------------- деньги

    async def test_write_expense_by_button(self):
        await self.click("do:money:add")
        self.assertIn("Что потратил", self.bot.texts[-1])

        await self.send("кофе 300")
        stored = await self.db.last_transactions(USER_ID)
        self.assertEqual(stored[0].amount, 30000)
        self.assertFalse(stored[0].is_income)

    async def test_write_income_by_button_without_plus(self):
        """В этом состоянии всё — доход, плюс печатать не нужно."""
        await self.click("do:money:income")
        await self.send("зарплата 90000")

        stored = await self.db.last_transactions(USER_ID)
        self.assertTrue(stored[0].is_income)
        self.assertEqual(stored[0].amount, 9_000_000)

    async def test_cancel_leaves_the_dialog(self):
        await self.click("do:money:add")
        self.assertIn("Отменил", await self.send("/cancel"))

        # после отмены обычный текст снова разбирается как всегда
        await self.send("120/80")
        self.assertEqual(len(await self.db.last_measurements(USER_ID)), 1)

    async def test_button_opens_its_section(self):
        await self.click("do:money:balance")
        user = await self.db.ensure_user(USER_ID)
        self.assertEqual(user.section, sections.MONEY)

    # -------------------------------------------------------- самочувствие

    async def test_health_prompt_has_ready_answers(self):
        await self.click("do:health:sleep")
        self.assertIn("Как спалось", self.bot.texts[-1])
        self.assertIn("7 ч", self.bot.last_buttons)

        await self.click("hm:sleep:420")
        stored = await self.db.get_metric(USER_ID, "sleep", self.today())
        self.assertEqual(stored.value, 420)

    async def test_already_recorded_is_mentioned(self):
        await self.db.set_metric(USER_ID, "steps", self.today(), 8000)
        await self.click("do:health:steps")
        self.assertIn("уже записано", self.bot.texts[-1])

    async def test_weight_asks_for_a_number(self):
        await self.click("do:health:weight")
        self.assertIn("весы", self.bot.texts[-1])

    # ----------------------------------------------------------- английский

    async def test_english_session_starts(self):
        await self.click("do:eng:cards")
        self.assertTrue(self.bot.last_buttons)  # варианты ответа

    async def test_english_progress(self):
        await self.click("do:eng:stats")
        self.assertIn("Английский", self.bot.texts[-1])

    # ------------------------------------------------------------ настройки

    async def test_settings_screen(self):
        buttons = await self.open("do:settings")
        self.assertIn("⏰ Напоминания", buttons)

        await self.click("do:set:remind")
        self.assertIn("напоминаний", self.bot.texts[-1])

        await self.click("do:set:tz")
        self.assertIn("Europe/Moscow", self.bot.texts[-1])

    async def test_update_from_the_menu(self):
        await self.click("do:set:update")
        self.assertIn("версия", self.bot.texts[-1].lower())
