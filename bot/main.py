"""Точка входа: настройка бота, планировщик напоминаний, long polling."""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .pressure import charts
from . import console
from .config import Config, load_config
from .db import Database
from .handlers import build_router
from .heartbeat import Heartbeat
from .journal import Counter, JournalMiddleware
from .middlewares import UserMiddleware, now_for
from .netlog import install as install_netlog
from .reminders import ReminderScheduler, next_fire, wait_text
from .updater import RESTART_CODE, UpdateWatcher, Updater
from .voice import Transcriber, build_transcriber

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
    palette = console.setup(config.log_level)
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

    counter = Counter()
    journal = JournalMiddleware(counter)
    middleware = UserMiddleware()
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.middleware(journal)  # первым: он же считает, сколько всё заняло
        observer.middleware(middleware)
    dispatcher.include_router(build_router())

    scheduler = ReminderScheduler(bot, db)
    watcher = UpdateWatcher(bot, db, updater, config.owner_id)
    heartbeat = Heartbeat(db, counter)

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

        scheduler.start()
        heartbeat.start()
        if config.auto_update_check and updater.is_git_repo():
            watcher.start()

        print(
            console.banner(
                await collect_startup(
                    me.username, db, config, updater, dispatcher["transcriber"]
                ),
                palette,
            )
        )
        return await _poll_until_stopped(
            dispatcher, bot, restart_event, config.polling_timeout
        )
    finally:
        await watcher.stop()
        await heartbeat.stop()
        await scheduler.stop()
        await db.close()
        await bot.session.close()


async def collect_startup(
    username: str,
    db: Database,
    config: Config,
    updater: Updater,
    transcriber: Transcriber,
) -> console.Startup:
    """Собирает шапку: версия, база, записи, ближайшее напоминание, что включено.

    Ни одна из этих справок не стоит того, чтобы из-за неё не запустился бот,
    поэтому всё, что может не получиться, получается «как выйдет».
    """
    info = console.Startup(username=username, tz=config.default_tz)

    if updater.is_git_repo():
        try:
            info.branch = await updater.branch()
            info.commit = await updater.commit()
        except Exception:  # noqa: BLE001 - git мог быть не настроен
            logger.debug("Не смог узнать версию", exc_info=True)

    info.db_path = config.db_path
    try:
        info.db_bytes = os.path.getsize(config.db_path)
    except OSError:
        info.db_bytes = 0

    owner = await db.owner_id()
    if owner is not None:
        settings = await db.ensure_user(owner)
        info.tz = settings.tz
        now = now_for(settings.tz)
        info.now = now

        pressure_count = await db.count_measurements(owner)
        money_count = await db.count_transactions(owner)
        words, learned = await db.eng_counts(owner)
        info.counts = [
            f"давление {pressure_count}",
            f"траты {money_count}",
            f"слова {learned}/{words}" if words else "слова —",
        ]

        reminders = await db.list_reminders(owner)
        info.reminders = len(reminders)
        upcoming = next_fire([item.at for item in reminders], now)
        if upcoming is not None:
            at, wait = upcoming
            info.next_reminder = f"{at:%H:%M} ({wait_text(wait)})"
    else:
        info.now = now_for(config.default_tz)
        info.counts = ["пока пусто — напиши боту /start"]

    info.features = [
        (
            "Графики",
            charts.available(),
            "matplotlib на месте" if charts.available() else "нет matplotlib: pip install matplotlib",
        ),
        ("Голос", transcriber.ready, "whisper.cpp на месте" if transcriber.ready else "выключен"),
        (
            "Автообновление",
            config.auto_update_check and updater.is_git_repo(),
            "проверяю раз в 6 часов"
            if config.auto_update_check and updater.is_git_repo()
            else "выключено",
        ),
    ]
    return info


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
