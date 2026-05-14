"""
Bitrix24 bot — FastAPI webhook-сервер.

Принимает сообщения от PHP-relay (BotWebhookRelay) на /webhook/bot,
передаёт в handlers.handle_message и возвращает JSON-ответ.
PHP-relay отправляет ответ пользователю через Bitrix\Im\Bot::addMessage().

Запуск:
  python -m bitrix24_bot.main
  (или через uvicorn: uvicorn bitrix24_bot.main:app --host 0.0.0.0 --port 7778)

.env переменные (см. .env.example):
  B24_BOT_PORT     — порт сервера (по умолчанию 7778)
  B24_WEBHOOK_TOKEN — токен для проверки X-Webhook-Token заголовка
"""
import logging
import os
import secrets
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path, чтобы импортировать config, adapters и т.д.
sys.path.insert(0, str(Path(__file__).parent.parent))

from logging.handlers import RotatingFileHandler

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def _setup_logging():
    logs_dir = Path(__file__).parent.parent / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        logs_dir / "bitrix24_bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[handler, logging.StreamHandler()],
    )


_setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Price Sync B24 Bot", docs_url=None, redoc_url=None)

_webhook_token = os.getenv("B24_WEBHOOK_TOKEN", "")


def _check_token(request: Request) -> bool:
    if not _webhook_token:
        return True  # токен не задан → dev-режим, пропускаем
    received = request.headers.get("X-Webhook-Token", "")
    return secrets.compare_digest(_webhook_token.encode(), received.encode())


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/bot")
async def handle_bot_webhook(request: Request):
    """
    Принимает сообщение от PHP-relay и возвращает JSON:
      {"messages": [{"text": "...", "keyboard": [...]}]}
    """
    if not _check_token(request):
        logger.warning("[B24Bot] Отклонён запрос с неверным токеном IP=%s", request.client.host)
        return JSONResponse(status_code=403, content={"error": "Forbidden"})

    try:
        form = await request.form()

        params: dict[str, str] = {}
        for key, value in form.items():
            if key.startswith("data[PARAMS][") and key.endswith("]"):
                params[key[13:-1]] = value

        dialog_id = params.get("DIALOG_ID", "")
        if not dialog_id:
            return JSONResponse(content={"messages": []})

        message = params.get("MESSAGE", "")
        command = params.get("COMMAND", "")
        command_params = params.get("COMMAND_PARAMS", "")
        from_user_id = int(params.get("FROM_USER_ID", "0") or "0")

        logger.info(
            "[B24Bot] user=%s cmd='%s' text='%s' dialog=%s",
            from_user_id, command, message[:60], dialog_id,
        )

        from bitrix24_bot.handlers import handle_message
        messages = await handle_message(
            dialog_id, from_user_id, message,
            command=command, command_params=command_params,
        )

        return JSONResponse(content={"messages": messages})

    except Exception as e:
        logger.error("[B24Bot] Ошибка обработки запроса: %s", e, exc_info=True)
        return JSONResponse(content={"messages": [{"text": "❌ Внутренняя ошибка сервера."}]})


def main():
    port = int(os.getenv("B24_BOT_PORT", "7778"))
    logger.info("Запуск B24-бота на порту %s", port)
    uvicorn.run(
        "bitrix24_bot.main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
