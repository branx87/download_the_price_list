import logging
from telegram import Bot
from telegram.ext import Application, CommandHandler, CallbackQueryHandler
import os
import sys
from dotenv import load_dotenv

# Настройка кодировки для Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

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
    button_callback
)

load_dotenv()
logger = logging.getLogger(__name__)

# Патч для бага в python-telegram-bot 22.x:
# Bot.initialize() устанавливает _initialized=True ДО вызова get_me().
# Если get_me() падает с TimedOut, при повторной попытке initialize() видит
# _initialized=True и пропускает get_me() — бот остаётся без _bot_user.
_orig_bot_initialize = Bot.initialize


async def _patched_bot_initialize(self: Bot) -> None:
    if self._initialized and self._bot_user is None:
        logger.warning("[FIX] Bot._initialized=True но _bot_user=None — сбрасываем флаг для повторного get_me()")
        self._initialized = False
    await _orig_bot_initialize(self)


Bot.initialize = _patched_bot_initialize


def main():
    """Запуск Telegram бота"""

    BOT_TOKEN = os.getenv('BOT_TOKEN', '')

    if not BOT_TOKEN:
        print("\n" + "="*60)
        print("❌ ОШИБКА: BOT_TOKEN не установлен!")
        print("="*60)
        print("\nДобавь в .env файл:")
        print("BOT_TOKEN=твой_токен_от_BotFather")
        print("\nПолучить токен: https://t.me/BotFather")
        print("="*60 + "\n")
        return

    # Создаем приложение с увеличенными таймаутами и поддержкой прокси
    from telegram.request import HTTPXRequest

    proxy_url = os.getenv('PROXY_URL', '') or None

    # Оба запроса нужно настроить явно — иначе get_updates_request получит
    # дефолтные 5-секундные таймауты, что вызывает TimedOut при медленной сети.
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        proxy=proxy_url,
    )
    get_updates_request = HTTPXRequest(
        connection_pool_size=1,
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        proxy=proxy_url,
    )

    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .get_updates_request(get_updates_request)
    )

    if proxy_url:
        print(f"🔄 Используется прокси: {proxy_url}")

    app = builder.build()

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

    # Обработчик кнопок
    app.add_handler(CallbackQueryHandler(button_callback))

    print("\nПодключаемся к Telegram... (может занять до минуты при медленной сети)")

    # post_init вызывается после успешной инициализации — выводим статус здесь
    async def on_ready(app: Application) -> None:
        logger.info("🤖 Telegram бот запущен! username=%s", app.bot.username)
        print("\n" + "="*60)
        print(f"🤖 TELEGRAM БОТ ЗАПУЩЕН (@{app.bot.username})")
        print("="*60)
        print("\n📱 Открой своего бота в Telegram и отправь /start")
        print("\n⌨️ Нажми Ctrl+C для остановки\n")

    app.post_init = on_ready

    # Запускаем polling с бесконечными повторами при сбоях сети
    app.run_polling(
        allowed_updates=['message', 'callback_query'],
        bootstrap_retries=-1,
    )


if __name__ == '__main__':
    main()
