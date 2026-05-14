# Развёртывание price_sync_bot в Bitrix24 (prod)

## 1. Bitrix24 сервер

### 1.1 Скопировать PHP-файлы

Из `c:\projects\integration-1c-bitrix\webhook_fastapi\PHP\` на сервер Bitrix:

| Локальный файл | Путь на сервере |
|---|---|
| `lib/Integration/BotWebhookRelay.php` | `/local/php_interface/lib/Integration/BotWebhookRelay.php` |
| `lib/Integration/PriceBotMessageHandler.php` | `/local/php_interface/lib/Integration/PriceBotMessageHandler.php` |
| `init_integration.php` | `/local/php_interface/init_integration.php` |

> `BotWebhookRelay.php` общий для lunch_bot и price_sync_bot — перезапишет старый, это ок.

### 1.2 Добавить переменные в `.env`

Файл: `/local/php_interface/.env` на сервере Bitrix.

```env
PRICE_BOT_CODE=price_sync_bot
FASTAPI_PRICE_BOT_HANDLER_URL=http://<ip_price_sync_bot>:7778/webhook/bot
FASTAPI_PRICE_BOT_WEBHOOK_TOKEN=<придумай_токен>
```

### 1.3 Зарегистрировать бота (один раз)

1. Скопировать `register_price_bot.php` (из `c:\projects\integration-1c-bitrix\webhook_fastapi\PHP\`) в `/local/` на сервере Bitrix
2. Найти ID администратора: открыть профиль в Bitrix24 → URL вида `/company/personal/user/788/`
3. Поправить `$USER->Authorize(788)` на правильный ID если нужно
4. Открыть в браузере: `https://<bitrix>/local/register_price_bot.php`
5. Записать полученный `BOT_ID=XXX`
6. **Удалить скрипт с сервера**: `rm /local/register_price_bot.php`

---

## 2. Сервер price_sync_bot

### 2.1 Добавить переменные в `.env`

```env
# Bitrix24 REST-вебхук (для отправки сообщений ботом)
# Создаётся в Bitrix24: Настройки → Входящий вебхук → права: im
BITRIX_REST_URL=https://<bitrix>/rest/<user_id>/<rest_token>/
BITRIX_BOT_ID=<BOT_ID из п.1.3>

# FastAPI-сервер бота
B24_BOT_PORT=7778
B24_WEBHOOK_TOKEN=<тот же токен что FASTAPI_PRICE_BOT_WEBHOOK_TOKEN>

# ID пользователей Bitrix24, которым разрешены команды (через запятую)
B24_ADMIN_IDS=788,123

# Чат для алертов (если нужно)
BITRIX_ALERT_CHAT_ID=chat22191
```

> `B24_WEBHOOK_TOKEN` == `FASTAPI_PRICE_BOT_WEBHOOK_TOKEN` — один и тот же секрет!

### 2.2 Запустить FastAPI-сервер

```bash
python -m bitrix24_bot.main
```

Или через systemd/supervisor если есть unit-файл.

---

## 3. Bitrix24 UI

- Открыть нужный групповой чат → **Добавить участника** → найти **Price Sync Bot** → добавить

---

## Проверка

1. Написать боту в чат: `помощь`
2. Должен ответить списком команд
3. Если не отвечает — смотреть лог: `/local/php_interface/logs/fastapi/price_bot_handler.log`
   и лог FastAPI-сервера

---

## Dev-значения (для справки)

| Параметр | Dev |
|---|---|
| BOT_ID | 903 |
| Bitrix URL | https://ers-dev |
| Admin user ID | 788 |
| lunch_bot BOT_ID | 835 |
| lunch_bot порт | 7777 |
| price_sync_bot порт | 7778 |
