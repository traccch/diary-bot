"""Точка входа: настройка бота, планировщик напоминаний, long polling."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramUnauthorizedError,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .pressure import charts
from . import console
from .config import Config, load_config
from .db import Database
from .handlers import build_router
from .formatting import duration
from .config import INSTALL, OFF
from .handlers.update import RESTART_NOTICE, ProgressReport, render_status
from .heartbeat import Heartbeat
from .journal import Counter, JournalMiddleware
from .middlewares import AccessMiddleware, UserMiddleware, now_for
from .netlog import install as install_netlog
from . import sysinfo
from .reminders import ReminderScheduler, next_fire, wait_text
from .updater import RESTART_CODE, UpdateWatcher, Updater
from .voice import Transcriber, build_transcriber

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="help", description="Меню: всё кнопками, без команд"),
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
    BotCommand(command="car", description="Пробег: сводка по машине"),
    BotCommand(command="status", description="Состояние бота и компьютера"),
    BotCommand(command="import", description="Загрузить операции файлом"),
    BotCommand(command="commands", description="Список всех команд"),
]


async def run() -> int:
    """Запускает бота. Возвращает код возврата: RESTART_CODE — «подними заново»."""
    config = load_config()
    palette = console.setup(config.log_level)
    install_netlog()

    db = Database(config.db_path, config.default_tz)
    await db.connect()

    try:
        session = build_session(config.proxy)
    except RuntimeError as exc:
        print(f"\n✗ {exc}\n")
        return 0

    bot = Bot(
        token=config.token,
        session=session,
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
    access = AccessMiddleware(config.allowed_users)
    middleware = UserMiddleware()
    for observer in (dispatcher.message, dispatcher.callback_query):
        observer.middleware(journal)  # первым: он же считает, сколько всё заняло
        observer.middleware(access)  # затем: чужого дальше пускать незачем
        observer.middleware(middleware)
    dispatcher.include_router(build_router())

    scheduler = ReminderScheduler(bot, db)
    heartbeat = Heartbeat(db, counter)
    dispatcher["heartbeat"] = heartbeat
    watcher = UpdateWatcher(
        bot,
        db,
        updater,
        config.owner_id,
        interval_hours=config.auto_update_minutes / 60,
        installer=(
            make_installer(bot, db, updater, restart_event)
            if config.auto_update == INSTALL
            else None
        ),
    )

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
        # после обновления сообщения, написанные в минуту перезапуска, надо
        # обработать: человек ждёт ответа и не знает, что бота в этот миг нет
        keep = await restarting_now(db)
        await bot.delete_webhook(drop_pending_updates=not keep)
        startup = await collect_startup(
            me.username, db, config, updater, dispatcher["transcriber"]
        )

        scheduler.start()
        heartbeat.start()
        if config.auto_update != OFF and updater.is_git_repo():
            watcher.start()

        await announce_restart(bot, db, startup.commit)
        print(
            console.banner(
                startup,
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


def make_installer(bot: Bot, db: Database, updater: Updater, restart_event: asyncio.Event):
    """Ставит обновление само, показывая всё то же, что и по кнопке.

    Автоматическое обновление осмысленно ровно потому, что перед перезапуском
    прогоняются тесты, а на красных бот откатывается сам. Кнопка «Обновить»
    при таком раскладе была формальностью — но молчать об установке нельзя:
    человек должен видеть, что и когда с его ботом произошло.
    """

    async def install(owner: int, status) -> None:
        head = await bot.send_message(
            owner,
            "⬇️ <b>Обновляюсь сам</b>\n" + render_status(status),
        )
        report = ProgressReport(head)
        await report.start()

        try:
            result = await updater.apply(progress=report)
        except Exception:  # noqa: BLE001 - обновление не должно ронять бота
            logger.exception("Автообновление сорвалось")
            await report.finish(
                "⚠️ Автообновление сорвалось, подробности в логах. "
                "Бот работает как работал."
            )
            return

        await report.finish(("✅ " if result.ok else "⚠️ ") + result.message)
        if result.restart:
            await db.set_meta("notified_commit", "")
            await db.set_meta(RESTART_NOTICE, f"{owner}|{time.time():.0f}")
            restart_event.set()

    return install


async def restarting_now(db: Database) -> bool:
    """Мы поднимаемся после обновления, а не запускаемся с нуля?

    Разница в том, что делать с сообщениями, накопившимися пока нас не было.
    После перезапуска их надо разобрать: их писали минуту назад, живому боту.
    А после того как ноутбук стоял выключенным неделю, разбирать команды
    недельной давности незачем — обстановка давно другая.
    """
    return bool(await db.get_meta(RESTART_NOTICE))


async def announce_restart(bot: Bot, db: Database, version: str) -> None:
    """Докладывает в чат, что бот вернулся после обновления.

    «Перезапускаюсь» без продолжения выглядит как зависший бот: человек ждёт
    ответа, которого не будет, потому что отвечать было уже некому — процесс
    в этот момент умирал.
    """
    notice = await db.get_meta(RESTART_NOTICE)
    if not notice:
        return
    await db.set_meta(RESTART_NOTICE, "")

    chat_id, _, stamp = notice.partition("|")
    took = ""
    try:
        took = duration(time.time() - float(stamp))
    except ValueError:
        pass

    text = f"✅ <b>Готово, я снова на связи</b> · {version}" if version else (
        "✅ <b>Готово, я снова на связи</b>"
    )
    if took:
        text += f"\n<i>Перезапуск занял {took}.</i>"
    try:
        await bot.send_message(int(chat_id), text)
    except (TelegramAPIError, ValueError):
        logger.debug("Не смог доложить о перезапуске", exc_info=True)


def build_session(proxy: str) -> Optional[AiohttpSession]:
    """Сессия с прокси, если он задан. Без прокси — обычная, как раньше."""
    if not proxy:
        return None
    try:
        return AiohttpSession(proxy=proxy)
    except ImportError as exc:  # aiohttp-socks ставится вместе с зависимостями
        raise RuntimeError(
            "Для прокси нужен пакет aiohttp-socks.\n"
            "  Установи зависимости заново: pip install -r requirements.txt"
        ) from exc


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
            info.commit = await updater.version() or await updater.commit()
        except Exception:  # noqa: BLE001 - git мог быть не настроен
            logger.debug("Не смог узнать версию", exc_info=True)

    info.machine = sysinfo.describe()
    info.db_path = console.short_path(config.db_path)
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
            "Доступ",
            True,
            f"только свои · {len(config.allowed_users)} в списке"
            if config.allowed_users
            else "только хозяин бота",
        ),
        (
            "Через прокси",
            bool(config.proxy),
            config.proxy or "напрямую",
        ),
        (
            "Обновления",
            config.auto_update != OFF and updater.is_git_repo(),
            {
                INSTALL: f"ставлю сам · смотрю раз в {config.auto_update_minutes} мин",
                "notify": f"спрашиваю · смотрю раз в {config.auto_update_minutes} мин",
            }.get(config.auto_update, "выключены")
            if updater.is_git_repo()
            else "не git-репозиторий",
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
