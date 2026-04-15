import asyncio
import logging
import os
import sys
from pathlib import Path

from telegram import Bot
from telegram.ext import Application, ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.error import NetworkError, TimedOut, Conflict
from telegram.request import HTTPXRequest
from dotenv import load_dotenv

from config.logging_config import setup_logging
from config.settings import settings

setup_logging(settings.LOG_DIR)

# Импортируем обработчики
from bot.handlers import (
    start_command,
    sync_command,
    sync_all_command,
    check_command,
    check_all_command,
    debug_command,
    status_command,
    vendors_command,
    help_command,
    erp_command,
    db_copy_command,
    synonyms_command,
    add_synonym_command,
    del_synonym_command,
    backfill_vff_command,
    duplicates_command,
    labor_command,
    labor_edit_command,
    button_callback,
    upload_price_handler,
)
from bot.scheduler import register_jobs

load_dotenv()
logger = logging.getLogger(__name__)

# Патч для бага в python-telegram-bot 22.x:
# Bot.initialize() устанавливает _initialized=True ДО вызова get_me().
# Если get_me() падает с TimedOut, при повторной попытке initialize() видит
# _initialized=True и пропускает get_me() — бот остаётся без _bot_user.
_orig_bot_initialize = Bot.initialize


async def _patched_bot_initialize(self: Bot) -> None:
    if getattr(self, '_initialized', False) and getattr(self, '_bot_user', None) is None:
        logger.warning("[FIX] Bot._initialized=True но _bot_user=None — сбрасываем флаг для повторного get_me()")
        self._initialized = False
    await _orig_bot_initialize(self)


Bot.initialize = _patched_bot_initialize

# Путь к PID-файлу для защиты от двойного запуска
_PID_FILE = Path(__file__).parent.parent / 'logs' / 'bot.pid'


def _check_and_write_pid() -> None:
    """Проверяем нет ли уже запущенного экземпляра. Пишем PID текущего процесса."""
    if _PID_FILE.exists():
        old_pid = _PID_FILE.read_text().strip()
        try:
            import psutil
            if psutil.pid_exists(int(old_pid)):
                print(f"\n❌ Бот уже запущен (PID {old_pid})!")
                print("Закрой предыдущий экземпляр и попробуй снова.\n")
                sys.exit(1)
        except ImportError:
            logger.warning("Найден старый PID-файл (%s). Убедись что бот не запущен дважды.", old_pid)

    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


async def error_handler(update: object, context) -> None:
    """Глобальный обработчик ошибок."""
    err = context.error
    if isinstance(err, (TimedOut, NetworkError)):
        logger.warning("[network] %s", err)
    elif isinstance(err, Conflict):
        logger.error("[conflict] Запущен второй экземпляр бота! %s", err)
    else:
        logger.error("[error] %s", err, exc_info=err)


async def main() -> None:
    """Запуск Telegram бота."""

    BOT_TOKEN = os.getenv('BOT_TOKEN', '')
    if not BOT_TOKEN:
        print("\n❌ ОШИБКА: BOT_TOKEN не установлен в .env файле\n")
        return

    _check_and_write_pid()
    app = None

    try:
        proxy_url = os.getenv('PROXY_URL', '') or None

        # Для обычных запросов: быстрый connect, нормальный write
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=15.0,
            read_timeout=30.0,
            write_timeout=30.0,
            pool_timeout=15.0,
            proxy=proxy_url,
        )
        # Для long-polling: read_timeout должен быть > polling timeout Telegram (30с)
        get_updates_request = HTTPXRequest(
            connection_pool_size=1,
            connect_timeout=15.0,
            read_timeout=90.0,
            write_timeout=30.0,
            pool_timeout=15.0,
            proxy=proxy_url,
        )

        if proxy_url:
            print(f"🔄 Используется прокси: {proxy_url}")

        app: Application = (
            ApplicationBuilder()
            .token(BOT_TOKEN)
            .request(request)
            .get_updates_request(get_updates_request)
            .build()
        )

        # Регистрируем обработчики команд
        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(CommandHandler("sync", sync_command))
        app.add_handler(CommandHandler("sync_all", sync_all_command))
        app.add_handler(CommandHandler("check", check_command))
        app.add_handler(CommandHandler("check_all", check_all_command))
        app.add_handler(CommandHandler("debug", debug_command))
        app.add_handler(CommandHandler("status", status_command))
        app.add_handler(CommandHandler("vendors", vendors_command))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("erp", erp_command))
        app.add_handler(CommandHandler("db_copy", db_copy_command))
        app.add_handler(CommandHandler("synonyms", synonyms_command))
        app.add_handler(CommandHandler("add_synonym", add_synonym_command))
        app.add_handler(CommandHandler("del_synonym", del_synonym_command))
        app.add_handler(CommandHandler("backfill_vff", backfill_vff_command))
        app.add_handler(CommandHandler("duplicates", duplicates_command))
        app.add_handler(CommandHandler("labor", labor_command))
        app.add_handler(CommandHandler("labor_edit", labor_edit_command))
        app.add_handler(CallbackQueryHandler(button_callback))
        app.add_handler(MessageHandler(filters.Document.ALL, upload_price_handler))
        app.add_error_handler(error_handler)

        # Регистрируем периодические задачи
        register_jobs(app)

        print("\nПодключаемся к Telegram...")

        await app.initialize()
        await app.start()

        bot_info = await app.bot.get_me()
        logger.info("Telegram бот запущен! username=%s", bot_info.username)
        print("\n" + "=" * 60)
        print(f"🤖 TELEGRAM БОТ ЗАПУЩЕН (@{bot_info.username})")
        print("=" * 60)
        print("\n📱 Открой своего бота в Telegram и отправь /start")
        print("\n⌨️ Нажми Ctrl+C для остановки\n")

        await app.updater.start_polling(
            allowed_updates=['message', 'callback_query'],
            drop_pending_updates=True,
            bootstrap_retries=5,
        )

        # Ждём сигнала остановки
        while True:
            await asyncio.sleep(1)

    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        logger.critical("Фатальная ошибка: %s", e, exc_info=True)
    finally:
        _remove_pid()
        if app is not None:
            try:
                if app.updater and app.updater.running:
                    await app.updater.stop()
                if app.running:
                    await app.stop()
                await app.shutdown()
            except Exception as e:
                logger.error("Ошибка при остановке: %s", e)
        logger.info("Бот остановлен.")


if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
