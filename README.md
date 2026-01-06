# Telegram Price Sync Bot 🤖

**Полный контроль прайсов через Telegram!**

## 🚀 Быстрый старт

### 1. Создай Telegram бота

1. Открой [@BotFather](https://t.me/BotFather)
2. Отправь `/newbot`
3. Следуй инструкциям
4. **СКОПИРУЙ ТОКЕН!**

### 2. Настрой проект

```bash
# Установи зависимости
pip install -r requirements.txt

# Настрой .env
cp .env.example .env
nano .env  # Вставь свой BOT_TOKEN
```

### 3. Запусти бота

```bash
python run_bot.py
```

### 4. Используй в Telegram

Найди своего бота и отправь:
```
/start
```

## 📱 Команды

| Команда | Описание |
|---------|----------|
| `/start` | Начать работу |
| `/sync` | Синхронизировать вендора (кнопки) |
| `/sync_all` | Синхронизировать всех |
| `/status` | Статус синхронизаций |
| `/vendors` | Список вендоров |
| `/help` | Справка |

## 💡 Пример использования

```
Ты: /sync

Бот: 📋 Выберите вендора:
     [🔄 KEAZ] [🔄 OWEN] ...

Ты: [Нажимаешь KEAZ]

Бот: 🚀 Синхронизация KEAZ запущена!
     ✅ KEAZ: Синхронизация завершена!
```

## 📁 Структура

```
telegram_price_bot/
├── bot/
│   ├── handlers.py    # Обработчики команд
│   └── main.py        # Запуск бота
├── config/
│   └── settings.py    # Настройки
├── run_bot.py         # 🚀 ЗАПУСК
├── requirements.txt
└── .env.example
```

## 🛠️ Настройка

### .env файл

```env
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
DATABASE_URL=твоя_база_данных
DKC_LOGIN=твой_логин
DKC_PASSWORD=твой_пароль
```

### Получить BOT_TOKEN

1. @BotFather в Telegram
2. `/newbot`
3. Скопировать токен

## 🎯 Возможности

✅ Управление с телефона  
✅ Кнопки выбора вендоров  
✅ Статистика в реальном времени  
✅ Работает откуда угодно  

## ❓ Проблемы?

**Бот не отвечает:**
- Проверь BOT_TOKEN в .env
- Убедись что run_bot.py запущен

**Ошибка модуля:**
```bash
pip install python-telegram-bot
```

## 🎉 Готово!

Теперь управляй прайсами через Telegram! 🚀
