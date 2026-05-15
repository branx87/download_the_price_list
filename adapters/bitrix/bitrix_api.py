"""
Bitrix24 REST API client.
BitrixBotAPI — отправляет сообщения через PHP-эндпоинт (приоритет) или imbot.message.add.
BitrixAlerter — шлёт уведомления в фиксированный групповой чат.
"""
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class BitrixBotAPI:
    """Отправляет сообщения в мессенджер Bitrix24."""

    def __init__(
        self,
        rest_url: str,
        bot_id: int,
        php_sender_url: str = "",
        php_sender_token: str = "",
    ):
        self._rest_url = rest_url.rstrip("/")
        self._bot_id = bot_id
        self._php_sender_url = php_sender_url
        self._php_sender_token = php_sender_token

    @property
    def is_configured(self) -> bool:
        return bool((self._rest_url or self._php_sender_url) and self._bot_id)

    async def send_message(
        self,
        dialog_id: str,
        text: str,
        keyboard: Optional[list] = None,
        replace: bool = False,
    ) -> bool:
        """Отправить сообщение от имени бота. PHP-эндпоинт имеет приоритет над REST API."""
        if self._php_sender_url:
            return await self._send_via_php(dialog_id, text, keyboard, replace)
        return await self._send_via_rest(dialog_id, text, keyboard)

    async def _send_via_php(
        self,
        dialog_id: str,
        text: str,
        keyboard: Optional[list] = None,
        replace: bool = False,
    ) -> bool:
        """Отправить через PHP-эндпоинт (обходит ограничение CLIENT_ID у imbot.message.add)."""
        payload: dict = {"bot_id": self._bot_id, "dialog_id": dialog_id, "message": text}
        if keyboard is not None:
            payload["keyboard"] = keyboard
        if replace:
            payload["replace"] = True
        headers = {}
        if self._php_sender_token:
            headers["X-Webhook-Token"] = self._php_sender_token
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(self._php_sender_url, json=payload, headers=headers)
                body = resp.text.strip()
                if resp.status_code == 200 and body:
                    try:
                        data = resp.json()
                    except Exception:
                        logger.warning("[BitrixBotAPI] PHP sender: не JSON (%s chars): %s", len(body), body[:200])
                        return False
                    if data.get("ok"):
                        logger.info("[BitrixBotAPI] PHP sender OK dialog=%s msg_id=%s", dialog_id, data.get("message_id"))
                        return True
                    logger.warning("[BitrixBotAPI] PHP sender error: %s", data.get("error", data))
                    return False
                logger.warning("[BitrixBotAPI] PHP sender HTTP %s пустое тело — файл не задеплоен? body=%r", resp.status_code, body[:100])
                return False
        except Exception as e:
            logger.error("[BitrixBotAPI] _send_via_php ошибка: %s", e)
            return False

    async def _send_via_rest(
        self,
        dialog_id: str,
        text: str,
        keyboard: Optional[list] = None,
    ) -> bool:
        """Отправить через imbot.message.add REST API."""
        if not self._rest_url:
            logger.warning("[BitrixBotAPI] Не настроен (нет BITRIX_REST_URL и BITRIX_BOT_SENDER_URL)")
            return False
        url = f"{self._rest_url}/imbot.message.add.json"
        payload: dict = {"BOT_ID": self._bot_id, "DIALOG_ID": dialog_id, "MESSAGE": text}
        if keyboard is not None:
            payload["KEYBOARD"] = keyboard
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("result"):
                        logger.debug("[BitrixBotAPI] Сообщение отправлено dialog=%s", dialog_id)
                        return True
                    logger.warning("[BitrixBotAPI] API error: %s", data.get("error_description", data))
                    return False
                logger.warning("[BitrixBotAPI] HTTP %s: %s", resp.status_code, resp.text[:200])
                return False
        except Exception as e:
            logger.error("[BitrixBotAPI] Ошибка отправки: %s", e)
            return False

    async def send_im_message(self, dialog_id: str, text: str) -> bool:
        """Отправить сообщение от имени REST-пользователя через im.message.add."""
        if not self._rest_url:
            return False
        url = f"{self._rest_url}/im.message.add.json"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json={"DIALOG_ID": dialog_id, "MESSAGE": text})
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("result"):
                        return True
                    logger.warning("[BitrixBotAPI] im.message.add error: %s", data.get("error_description", data))
                    return False
                logger.warning("[BitrixBotAPI] im.message.add HTTP %s", resp.status_code)
                return False
        except Exception as e:
            logger.error("[BitrixBotAPI] im.message.add ошибка: %s", e)
            return False


def _html_to_bbcode(text: str) -> str:
    text = re.sub(r'<b>(.*?)</b>', r'[B]\1[/B]', text, flags=re.DOTALL)
    text = re.sub(r'<code>(.*?)</code>', r'[CODE]\1[/CODE]', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    return text


class BitrixAlerter:
    """Отправляет алерты в групповой чат Bitrix24 через im.message.add (REST-пользователь)."""

    def __init__(self, api: BitrixBotAPI, chat_id: str) -> None:
        self._api = api
        self._chat_id = chat_id

    @property
    def is_configured(self) -> bool:
        return bool(self._api._rest_url and self._chat_id)

    async def send(self, text: str) -> None:
        if not self.is_configured:
            logger.warning("[BitrixAlerter] Не настроен (нет BITRIX_REST_URL или BITRIX_ALERT_CHAT_ID)")
            return
        bbcode_text = _html_to_bbcode(text)
        logger.info("[BitrixAlerter] Отправка в чат %s", self._chat_id)
        ok = await self._api.send_im_message(self._chat_id, bbcode_text)
        if not ok:
            logger.warning("[BitrixAlerter] Не удалось отправить в чат %s", self._chat_id)
