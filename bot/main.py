"""Точка входа: настройка бота, планировщик напоминаний, long polling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .pressure import charts
from .config import load_config
from .db import Database
from .handlers import build_router
from .middlewares import UserMiddleware
from .netlog import install as install_netlog
from .reminders import ReminderScheduler
from .updater import RESTART_CODE, UpdateWatcher, Updater
from .voice import build_transcriber

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="menu", description="Разделы: давление, деньги, английский"),
    BotCommand(command="eng", description="Английский: карточки на 3 минуты"),
    BotCommand(command="quest", description="Английский: квест-сцена"),
    BotCommand(command="stats", description="Сводка текущего раздела"),
    BotCommand(command="last", description="Последние записи"),
    BotCommand(command="undo", description="Удалить последнюю"),
    BotCommand(command="chart", description="График"),
    BotCommand(command="balance", description="Баланс за месяц"),
    BotCommand(command="export", description="Выгрузка"),
    BotCommand(command="remind", description="Напоминания"),
    BotCommand(command="limit", description="Лимит на месяц"),
    BotCommand(command="update", description="Обновить бота"),
    BotCommand(command="help", description="Как пользоваться"),
]


async def run() -> int:
    """Запускает бота. Возвращает код возврата: RESTART_CODE — «подними заново»."""
    config = load_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    install_netlog()

    db = Database(config.db_path, config.default_tz)
    await db.connect()

    bot = Bot(
        token=config.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    updater = Updater()
    restart_event = asyncio.Event()

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["db"] = db
    dispatcher["updater"] = updater
    dispatcher["owner_id"] = config.owner_id
    dispatcher["restart_event"] = restart_event
    dispatcher["transcriber"] = build_transcriber(config.voice)

    middleware = UserMiddleware()
    dispatcher.message.middleware(middleware)
    dispatcher.callback_query.middleware(middleware)
    dispatcher.include_router(build_router())

    scheduler = ReminderScheduler(bot, db)
    watcher = UpdateWatcher(bot, db, updater, config.owner_id)

    try:
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            print(
                "\n✗ Telegram не принял токен.\n"
                "  Проверь строку BOT_TOKEN в файле .env — она должна быть ровно такой,\n"
                "  какую прислал @BotFather. Если бот удалён или токен отозван,\n"
                "  получи новый через /newbot или /token у @BotFather.\n"
            )
            return 0
        except TelegramNetworkError:
            print(
                "\n✗ Не получается достучаться до Telegram.\n"
                "  Проверь интернет. Если Telegram блокируется провайдером,\n"
                "  запусти бота через VPN.\n"
            )
            return 0

        await bot.set_my_commands(COMMANDS)
        await bot.delete_webhook(drop_pending_updates=True)
        if not charts.available():
            logger.warning(
                "matplotlib не установлен — графики и PDF будут недоступны "
                "(pip install matplotlib)"
            )

        scheduler.start()
        if config.auto_update_check and updater.is_git_repo():
            watcher.start()

        logger.info("Бот @%s запущен", me.username)
        print(f"\n✓ Бот работает: https://t.me/{me.username}\n")
        return await _poll_until_stopped(
            dispatcher, bot, restart_event, config.polling_timeout
        )
    finally:
        await watcher.stop()
        await scheduler.stop()
        await db.close()
        await bot.session.close()


async def _poll_until_stopped(
    dispatcher: Dispatcher,
    bot: Bot,
    restart_event: asyncio.Event,
    polling_timeout: int = 15,
) -> int:
    """Крутит long polling, пока бота не остановят или он не попросит перезапуск."""
    polling = asyncio.create_task(
        dispatcher.start_polling(bot, polling_timeout=polling_timeout)
    )
    waiting = asyncio.create_task(restart_event.wait())

    done, _ = await asyncio.wait(
        {polling, waiting}, return_when=asyncio.FIRST_COMPLETED
    )

    if waiting in done:
        # stop_polling ждёт завершения цикла, поэтому зовём его снаружи обработчика
        await dispatcher.stop_polling()
        await polling
        logger.info("Перезапуск после обновления")
        return RESTART_CODE

    waiting.cancel()
    await polling
    return 0


def main() -> None:
    try:
        code = asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлен")
        return
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
