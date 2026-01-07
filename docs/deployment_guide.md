# Руководство по развертыванию Price Sync Bot

## Быстрый старт (5 минут)

### Шаг 1: Клонирование репозитория

```bash
git clone <repository-url>
cd price_sync_bot
```

### Шаг 2: Создание виртуального окружения

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### Шаг 3: Установка зависимостей

```bash
pip install -r requirements.txt
```

### Шаг 4: Создание Telegram бота

1. Откройте [@BotFather](https://t.me/BotFather) в Telegram
2. Отправьте команду `/newbot`
3. Введите название бота: `My Price Sync Bot`
4. Введите username: `my_price_sync_bot`
5. **Скопируйте токен** (формат: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)

### Шаг 5: Настройка .env файла

Создайте файл `.env` в корне проекта:

```bash
# .env
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
DATABASE_URL=sqlite:///./prices.db
DKC_LOGIN=your_dkc_login
DKC_PASSWORD=your_dkc_password

# Опционально: прокси (если Telegram заблокирован)
# PROXY_URL=http://proxy.example.com:8080
```

### Шаг 6: Инициализация базы данных

```bash
python init_db_universal.py
```

### Шаг 7: Запуск бота

```bash
python -m bot.main
```

**Готово!** Откройте бота в Telegram и отправьте `/start`

---

## Подробная инструкция

### Системные требования

**Минимальные:**
- Python 3.11+
- 2 GB RAM
- 1 GB свободного места на диске

**Рекомендуемые:**
- Python 3.12+
- 4 GB RAM
- 5 GB свободного места (для логов и прайсов)

**Операционные системы:**
- Windows 10/11
- Ubuntu 20.04+
- macOS 11+

### Зависимости

```txt
python-telegram-bot>=20.0    # Telegram Bot API
sqlalchemy>=2.0.0            # ORM для БД
pandas>=2.0.0                # Обработка данных
openpyxl>=3.1.0              # Чтение .xlsx
xlrd>=2.0.0                  # Чтение .xls
requests>=2.31.0             # HTTP клиент
beautifulsoup4>=4.12.0       # HTML парсинг
python-dotenv>=1.0.0         # Переменные окружения
```

### Настройка переменных окружения

#### Обязательные

**BOT_TOKEN**
- Токен Telegram бота от @BotFather
- Формат: `числа:буквы_и_цифры`
- Пример: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`

**DATABASE_URL**
- URL подключения к базе данных
- SQLite: `sqlite:///./prices.db`
- PostgreSQL: `postgresql://user:password@localhost:5432/prices`

#### Опциональные

**DKC_LOGIN**, **DKC_PASSWORD**
- Учетные данные для доступа к прайсам DKC
- Требуются только если синхронизируете DKC

**PROXY_URL**
- URL прокси-сервера (если Telegram заблокирован)
- Форматы:
  - HTTP: `http://proxy.example.com:8080`
  - SOCKS5: `socks5://user:pass@proxy.example.com:1080`

**PRICE_CHANGE_THRESHOLD**
- Порог значимого изменения цены (по умолчанию: 0.01)
- Изменения меньше порога игнорируются

---

## Развертывание в Production

### Вариант 1: Systemd (Linux)

**1. Создайте systemd service файл:**

```bash
sudo nano /etc/systemd/system/price-sync-bot.service
```

```ini
[Unit]
Description=Price Sync Telegram Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/price_sync_bot
Environment="PATH=/path/to/price_sync_bot/venv/bin"
ExecStart=/path/to/price_sync_bot/venv/bin/python -m bot.main
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**2. Активируйте сервис:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable price-sync-bot
sudo systemctl start price-sync-bot
```

**3. Проверьте статус:**

```bash
sudo systemctl status price-sync-bot
```

**4. Логи:**

```bash
# Просмотр логов
sudo journalctl -u price-sync-bot -f

# Последние 100 строк
sudo journalctl -u price-sync-bot -n 100
```

### Вариант 2: Docker

**Создайте Dockerfile:**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY . .

# Создание директорий
RUN mkdir -p logs price_files reports

# Переменные окружения
ENV PYTHONUNBUFFERED=1

# Запуск
CMD ["python", "-m", "bot.main"]
```

**docker-compose.yml:**

```yaml
version: '3.8'

services:
  bot:
    build: .
    container_name: price_sync_bot
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./logs:/app/logs
      - ./price_files:/app/price_files
      - ./reports:/app/reports
      - ./prices.db:/app/prices.db
```

**Запуск:**

```bash
docker-compose up -d
```

**Логи:**

```bash
docker-compose logs -f bot
```

### Вариант 3: Windows Service (NSSM)

**1. Скачайте [NSSM](https://nssm.cc/download)**

**2. Установите сервис:**

```cmd
nssm install PriceSyncBot "C:\path\to\venv\Scripts\python.exe" "-m bot.main"
nssm set PriceSyncBot AppDirectory "C:\path\to\price_sync_bot"
nssm set PriceSyncBot AppEnvironmentExtra "PYTHONUNBUFFERED=1"
nssm start PriceSyncBot
```

---

## Настройка базы данных

### SQLite (по умолчанию)

**Преимущества:**
- Нулевая настройка
- Один файл БД
- Достаточно для малых/средних объемов

**Ограничения:**
- Один писатель одновременно
- Не рекомендуется для >10M записей

**Создание БД:**

```bash
python init_db_universal.py
```

**Бэкап:**

```bash
# Копирование файла
cp prices.db prices_backup_$(date +%Y%m%d).db

# Или через sqlite3
sqlite3 prices.db ".backup prices_backup.db"
```

### PostgreSQL (рекомендуется для production)

**1. Установка PostgreSQL:**

```bash
# Ubuntu
sudo apt update
sudo apt install postgresql postgresql-contrib

# Создание БД и пользователя
sudo -u postgres psql
CREATE DATABASE prices;
CREATE USER pricebot WITH PASSWORD 'strong_password';
GRANT ALL PRIVILEGES ON DATABASE prices TO pricebot;
\q
```

**2. Обновите .env:**

```bash
DATABASE_URL=postgresql://pricebot:strong_password@localhost:5432/prices
```

**3. Создайте таблицы:**

```sql
CREATE TABLE Total_Price (
    id SERIAL PRIMARY KEY,
    Vendor VARCHAR(50) NOT NULL,
    Part_Num VARCHAR(100) NOT NULL,
    Descr TEXT,
    Price DECIMAL(10, 2),
    Units VARCHAR(20),
    Storage VARCHAR(100),
    Status VARCHAR(20) DEFAULT 'active',
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(Vendor, Part_Num)
);

CREATE INDEX idx_vendor_article ON Total_Price(Vendor, Part_Num);
CREATE INDEX idx_vendor ON Total_Price(Vendor);
CREATE INDEX idx_status ON Total_Price(Status);
```

**4. Перенос данных из SQLite:**

```bash
# Экспорт из SQLite
sqlite3 prices.db .dump > export.sql

# Импорт в PostgreSQL (с адаптацией синтаксиса)
psql -U pricebot -d prices < export.sql
```

---

## Мониторинг и логирование

### Структура логов

```
logs/
├── sync.log           # Общие логи синхронизации
└── bot.log            # Логи Telegram бота (опционально)
```

### Настройка логирования

**main.py:**
```python
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/sync.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
```

**Ротация логов:**

```python
# Добавьте RotatingFileHandler
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(
    'logs/sync.log',
    maxBytes=10_000_000,  # 10MB
    backupCount=5,
    encoding='utf-8'
)
```

### Мониторинг через Telegram

Бот имеет встроенные команды для мониторинга:

- `/status` - статус последних синхронизаций
- `/debug` - подробные логи ошибок
- `/check` - проверка актуальности без обновления

### Внешний мониторинг

**Healthcheck endpoint (рекомендация для будущего):**

```python
# healthcheck.py
from datetime import datetime, timedelta
from adapters.database.sql_repository import SqlRepository

def check_sync_health():
    """Проверка что синхронизация выполнялась недавно"""
    repo = SqlRepository('sqlite:///./prices.db')

    for vendor in ['KEAZ', 'OWEN', 'EKF', 'IEK', 'DKC', 'CHINT']:
        last_update = repo.get_vendor_last_update(vendor)

        if not last_update:
            print(f"❌ {vendor}: нет данных")
            continue

        age = datetime.now() - last_update
        if age > timedelta(days=7):
            print(f"⚠️ {vendor}: обновлялся {age.days} дней назад")
        else:
            print(f"✅ {vendor}: актуален")
```

**Cron задача:**

```bash
# Проверка каждые 4 часа
0 */4 * * * cd /path/to/price_sync_bot && /path/to/venv/bin/python healthcheck.py
```

---

## Автоматизация синхронизации

### Cron (Linux/Mac)

**Ежедневная синхронизация в 3:00:**

```bash
crontab -e
```

```cron
# Синхронизация всех вендоров
0 3 * * * cd /path/to/price_sync_bot && /path/to/venv/bin/python main.py >> logs/cron.log 2>&1

# Или отдельные задачи
0 3 * * * cd /path/to/price_sync_bot && /path/to/venv/bin/python main.py KEAZ
30 3 * * * cd /path/to/price_sync_bot && /path/to/venv/bin/python main.py OWEN
0 4 * * * cd /path/to/price_sync_bot && /path/to/venv/bin/python main.py IEK
```

### Task Scheduler (Windows)

**PowerShell скрипт (sync_all.ps1):**

```powershell
$venvPython = "C:\path\to\price_sync_bot\venv\Scripts\python.exe"
$mainScript = "C:\path\to\price_sync_bot\main.py"
$logFile = "C:\path\to\price_sync_bot\logs\scheduled.log"

Set-Location "C:\path\to\price_sync_bot"
& $venvPython $mainScript *>> $logFile
```

**Создание задачи:**

1. Откройте Task Scheduler
2. Create Task → General:
   - Name: `Price Sync Bot`
   - Run with highest privileges
3. Triggers → New:
   - Daily at 3:00 AM
4. Actions → New:
   - Program: `powershell.exe`
   - Arguments: `-File "C:\path\to\sync_all.ps1"`

---

## Обслуживание

### Очистка старых файлов

```bash
# Удаление прайсов старше 30 дней
find price_files -name "*.xls*" -mtime +30 -delete

# Удаление старых логов
find logs -name "*.log.*" -mtime +90 -delete
```

### Оптимизация БД

**SQLite:**

```bash
sqlite3 prices.db "VACUUM;"
sqlite3 prices.db "ANALYZE;"
```

**PostgreSQL:**

```sql
VACUUM ANALYZE Total_Price;
REINDEX TABLE Total_Price;
```

### Бэкап

**Автоматический бэкап (cron):**

```bash
#!/bin/bash
# backup.sh

BACKUP_DIR="/backups/price_sync_bot"
DATE=$(date +%Y%m%d_%H%M%S)

# Создание директории
mkdir -p "$BACKUP_DIR"

# Бэкап БД
sqlite3 /path/to/prices.db ".backup $BACKUP_DIR/prices_$DATE.db"

# Бэкап конфигурации
tar -czf "$BACKUP_DIR/config_$DATE.tar.gz" \
    /path/to/.env \
    /path/to/vendors/registry.py

# Удаление старых бэкапов (>30 дней)
find "$BACKUP_DIR" -name "*.db" -mtime +30 -delete
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +30 -delete
```

```cron
# Ежедневный бэкап в 2:00
0 2 * * * /path/to/backup.sh
```

---

## Troubleshooting

### Проблема: Бот не отвечает

**Диагностика:**

```bash
# Проверка процесса
ps aux | grep "bot.main"

# Проверка логов
tail -n 100 logs/sync.log
```

**Решение:**

```bash
# Перезапуск
sudo systemctl restart price-sync-bot

# Или через Docker
docker-compose restart bot
```

### Проблема: Telegram API недоступен

**Симптомы:**
```
telegram.error.NetworkError: urllib3.exceptions.ConnectTimeoutError
```

**Решение 1: Настройка прокси**

```bash
# .env
PROXY_URL=http://proxy.example.com:8080
```

**Решение 2: Увеличение таймаутов**

```python
# bot/main.py
request = HTTPXRequest(
    connect_timeout=60.0,  # Увеличено с 30
    read_timeout=60.0,
    write_timeout=60.0
)
```

### Проблема: Ошибка парсинга Excel

**Симптомы:**
```
ValueError: Не найдена строка заголовков
```

**Решение:**

1. Проверьте формат файла вручную
2. Обновите конфигурацию в `vendors/registry.py`:

```python
'VENDOR': VendorConfig(
    parser_config={
        'columns': {
            'article': 'Новое название колонки',
            ...
        }
    }
)
```

### Проблема: База данных заблокирована

**Симптомы:**
```
sqlite3.OperationalError: database is locked
```

**Решение:**

```bash
# Проверка процессов
lsof prices.db

# Завершение всех процессов
pkill -f "python.*main.py"

# Перезапуск с одним процессом
python main.py KEAZ
```

### Проблема: Память заполнена

**Симптомы:**
```
MemoryError
```

**Решение 1: Очистка логов**

```bash
# Удалить старые логи
rm logs/*.log.*

# Очистить текущий лог
> logs/sync.log
```

**Решение 2: Увеличение swap (Linux)**

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

---

## Обновление

### Обновление кода

```bash
# Остановка бота
sudo systemctl stop price-sync-bot

# Обновление из Git
git pull origin main

# Обновление зависимостей
source venv/bin/activate
pip install -r requirements.txt --upgrade

# Перезапуск
sudo systemctl start price-sync-bot
```

### Миграция БД (при изменении схемы)

**Рекомендация: Используйте Alembic**

```bash
pip install alembic
alembic init migrations
```

**Пример миграции:**

```python
# migrations/versions/001_add_status_column.py
def upgrade():
    op.add_column('Total_Price',
        sa.Column('Status', sa.String(20), server_default='active')
    )

def downgrade():
    op.drop_column('Total_Price', 'Status')
```

```bash
# Применение миграции
alembic upgrade head
```

---

## Масштабирование

### Горизонтальное масштабирование

**Разделение ботов по функциям:**

```yaml
# docker-compose.yml
services:
  bot-sync:
    build: .
    command: python -m bot.main
    environment:
      - BOT_MODE=sync

  bot-status:
    build: .
    command: python -m bot.main
    environment:
      - BOT_MODE=status
```

### Вертикальное масштабирование

**Увеличение ресурсов для БД:**

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: prices
    command:
      - "postgres"
      - "-c"
      - "shared_buffers=256MB"
      - "-c"
      - "max_connections=200"
```

---

## Безопасность

### 1. Защита .env файла

```bash
# Права доступа только для владельца
chmod 600 .env

# Не коммитить в Git
echo ".env" >> .gitignore
```

### 2. Ограничение доступа к боту

```python
# bot/handlers.py
ALLOWED_USERS = [123456789, 987654321]  # Telegram User IDs

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    ...
```

### 3. HTTPS для PostgreSQL

```bash
# .env
DATABASE_URL=postgresql://user:pass@localhost:5432/prices?sslmode=require
```

### 4. Регулярные обновления

```bash
# Обновление зависимостей с патчами безопасности
pip list --outdated
pip install --upgrade <package>
```

---

## Чек-лист развертывания

- [ ] Python 3.11+ установлен
- [ ] Виртуальное окружение создано
- [ ] Зависимости установлены
- [ ] .env файл настроен
- [ ] Telegram бот создан через @BotFather
- [ ] База данных инициализирована
- [ ] Бот запускается и отвечает на /start
- [ ] Синхронизация работает (тест на одном вендоре)
- [ ] Логи пишутся в logs/
- [ ] Systemd/Docker настроен для автозапуска
- [ ] Бэкапы настроены
- [ ] Мониторинг настроен
- [ ] Права доступа к боту ограничены

---

**Составлено:** Claude Sonnet 4.5
**Дата:** 2026-01-07
