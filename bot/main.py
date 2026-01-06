import logging
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
    status_command,
    vendors_command,
    help_command,
    button_callback
)

load_dotenv()
logger = logging.getLogger(__name__)


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
    builder = Application.builder().token(BOT_TOKEN)

    # Настройка таймаутов
    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )
    builder.request(request)

    # Если есть прокси в .env, используем его
    proxy_url = os.getenv('PROXY_URL', '')
    if proxy_url:
        builder.proxy_url(proxy_url)
        print(f"🔄 Используется прокси: {proxy_url}")

    app = builder.build()

    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("sync_all", sync_all_command))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("vendors", vendors_command))
    app.add_handler(CommandHandler("help", help_command))

    # Обработчик кнопок
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("🤖 Telegram бот запущен!")
    print("\n" + "="*60)
    print("🤖 TELEGRAM БОТ ЗАПУЩЕН")
    print("="*60)
    print("\n📱 Открой своего бота в Telegram и отправь /start")
    print("\n⌨️ Нажми Ctrl+C для остановки\n")

    # Запускаем polling
    app.run_polling(allowed_updates=['message', 'callback_query'])


if __name__ == '__main__':
    main()
