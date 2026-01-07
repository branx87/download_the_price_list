# Анализ кодовой базы: Price Sync Bot

> Telegram-бот для автоматической синхронизации прайс-листов поставщиков электротехнического оборудования

**Дата анализа:** 2026-01-07
**Уровень сложности:** Middle/Senior friendly
**Архитектурный подход:** Domain-Driven Design (DDD) + Clean Architecture

---

## Структура проекта

```
price_sync_bot/
├── adapters/              # Адаптеры для внешних систем
│   ├── database/         # Работа с БД (SQLAlchemy)
│   │   └── sql_repository.py
│   ├── downloaders/      # Загрузчики прайс-листов
│   │   ├── base_downloader.py
│   │   ├── simple_http.py
│   │   ├── auth_http.py
│   │   ├── iek_downloader.py
│   │   ├── dkc_downloader.py
│   │   └── chint_downloader.py
│   └── parsers/          # Парсеры Excel файлов
│       └── excel_parser.py
│
├── bot/                   # Telegram бот
│   ├── main.py           # Точка входа бота
│   ├── handlers.py       # Обработчики команд
│   └── keyboards.py      # Клавиатуры бота
│
├── domain/                # Бизнес-логика (чистый домен)
│   ├── entities/         # Сущности
│   │   ├── price_item.py
│   │   ├── sync_result.py
│   │   └── price_comparison.py
│   ├── interfaces/       # Абстракции (порты)
│   │   ├── downloader.py
│   │   ├── parser.py
│   │   └── repository.py
│   └── services/         # Доменные сервисы
│       ├── sync_service.py
│       ├── report_service.py
│       └── data_normalizer.py
│
├── vendors/               # Конфигурации вендоров
│   └── registry.py       # Реестр всех поставщиков
│
├── utils/                 # Утилиты
│   └── normalizer.py     # Нормализация артикулов
│
├── config/                # Конфигурация
│   └── settings.py       # Настройки приложения
│
├── main.py               # CLI точка входа (синхронизация)
├── requirements.txt      # Зависимости
├── .env                  # Переменные окружения
└── prices.db             # SQLite база данных
```

### Принципы организации кода

**1. Domain-Driven Design (DDD)**
- Чистый домен изолирован от внешних зависимостей
- Бизнес-логика сосредоточена в `domain/`
- Зависимости направлены внутрь (к домену)

**2. Dependency Injection**
- Все зависимости передаются через конструкторы
- Легкая замена реализаций (например, разные загрузчики для разных вендоров)

**3. Interface Segregation**
- Четкие интерфейсы для каждой роли: `IDownloader`, `IParser`, `IRepository`

---

## Технологический стек

| Компонент | Технология | Версия | Назначение |
|-----------|-----------|--------|-----------|
| **Язык** | Python | 3.11+ | Основной язык разработки |
| **Бот** | python-telegram-bot | >=20.0 | Telegram Bot API |
| **БД** | SQLAlchemy | >=2.0.0 | ORM и работа с SQLite |
| **Парсинг** | pandas | >=2.0.0 | Обработка табличных данных |
| **Excel** | openpyxl, xlrd | >=3.1.0, >=2.0.0 | Чтение .xlsx и .xls файлов |
| **HTTP** | requests | >=2.31.0 | Загрузка файлов |
| **HTML** | beautifulsoup4 | >=4.12.0 | Парсинг веб-страниц |
| **Конфигурация** | python-dotenv | >=1.0.0 | Управление переменными окружения |

### Особенности стека

- **SQLite** используется для простоты развертывания (один файл БД)
- **pandas** для мощной обработки Excel с поддержкой различных форматов
- **SQLAlchemy Core** (не ORM) для прямого SQL с типобезопасностью

---

## Архитектурные паттерны

### 1. Clean Architecture + Hexagonal (Ports & Adapters)

```python
# Порт (интерфейс в domain)
class IRepository(ABC):
    @abstractmethod
    def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
        pass

# Адаптер (реализация в adapters)
class SqlRepository(IRepository):
    def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
        # SQL реализация
        ...
```

**Преимущества:**
- Бизнес-логика не зависит от БД
- Легко заменить SQLite на PostgreSQL
- Тестируемость через моки

### 2. Strategy Pattern (Стратегия загрузки)

Разные вендоры требуют разных способов загрузки прайсов:

```python
# IEK - требует JavaScript рендеринг
class IekDownloader(BaseDownloader):
    def download(self, vendor: str) -> Path:
        # Selenium для обхода JavaScript
        ...

# KEAZ - простой HTTP
class SimpleHttpDownloader(IDownloader):
    def download(self, vendor: str) -> Path:
        response = requests.get(self.url)
        ...

# DKC - требует авторизацию
class DkcDownloader(BaseDownloader):
    def download(self, vendor: str) -> Path:
        session.post('/login', data={...})
        ...
```

### 3. Registry Pattern (Реестр вендоров)

Централизованная конфигурация всех поставщиков:

```python
class VendorRegistry:
    def _init_vendors(self) -> Dict[str, VendorConfig]:
        return {
            'KEAZ': VendorConfig(
                downloader_class=SimpleHttpDownloader,
                downloader_params={'url': 'https://files.keaz.ru/ftp/keaz.xls'},
                parser_config={'columns': {...}}
            ),
            'IEK': VendorConfig(
                downloader_class=IekDownloader,
                ...
            )
        }
```

**Преимущества:**
- Добавление нового вендора = добавление конфигурации
- Нет дублирования кода

### 4. Value Object (Сущности домена)

```python
@dataclass
class PriceItem:
    vendor: str
    article: str
    description: str
    price: Decimal
    units: str = "шт"

    def has_price_changed(self, other: 'PriceItem', threshold: float) -> bool:
        return abs(float(self.price) - float(other.price)) > threshold
```

**Особенности:**
- Иммутабельные объекты через `@dataclass`
- Бизнес-логика инкапсулирована в методы

### 5. Service Layer

```python
class SyncService:
    def sync_vendor(self, vendor: str) -> SyncResult:
        # 1. Загрузка
        file_path = self.downloader.download(vendor)

        # 2. Парсинг
        new_items = self.parser.parse(file_path, vendor)

        # 3. Сравнение
        current_items = self.repository.get_items_by_vendor(vendor)

        # 4. Применение изменений
        self.repository.add_items(to_add)
        self.repository.update_items(to_update)

        return result
```

---

## Ключевые компоненты

### 1. SyncService - Сервис синхронизации

**Назначение:** Оркестрирует процесс синхронизации прайс-листов

**Основной метод:**
```python
def sync_vendor(self, vendor: str) -> SyncResult:
    # 1. Загрузка файла
    file_path = self.downloader.download(vendor)

    # 2. Парсинг
    new_items = self.parser.parse(file_path, vendor)

    # 3. Получение текущих данных
    current_items = self.repository.get_items_by_vendor(vendor)

    # 4. Анализ изменений
    to_add = new_items_with_price - current_articles
    to_update = items_with_price_changes
    disappeared = current_articles - new_articles

    # 5. Применение изменений
    self.repository.add_items(to_add)
    self.repository.update_items(to_update)
    self.repository.mark_as_disappeared(vendor, disappeared)

    return result
```

**Зависимости:**
- `IDownloader` - загрузка файлов
- `IParser` - парсинг Excel
- `IRepository` - работа с БД
- `ReportService` - генерация отчетов

**Интересная особенность:** Обработка заказных позиций (цена = 0)
```python
# Фильтруем заказные позиции для добавления
new_items_with_price = [item for item in new_items if float(item.price) > 0]

# Но учитываем их при определении исчезнувших
disappeared_articles = current_articles - new_articles  # включая с ценой 0
```

---

### 2. ExcelParser - Универсальный парсер

**Назначение:** Парсит Excel файлы различных форматов в единую структуру данных

**Сложность:** Каждый вендор имеет свой формат Excel:
- Разные строки заголовков
- Объединенные ячейки
- Вложенные заголовки
- Группировки товаров

**Пример обработки IEK (сложный случай):**
```python
# IEK: строка 5 - основные заголовки, строка 6 - подзаголовки
header_main = df.iloc[5]
header_sub = df.iloc[6]

# Умное объединение заголовков
for main, sub in zip(header_main, header_sub):
    # Для колонок с ценой объединяем
    if 'цена' in main.lower() and sub:
        combined = f"{main} {sub}"  # "Базовая цена с НДС"
    # Если email - используем подзаголовок
    elif '@' in main and sub:
        combined = sub
    else:
        combined = main
```

**Нормализация данных:**
```python
def _clean_price(self, price_val: Any) -> float:
    # Обработка "под запрос", "договорная"
    if any(x in price_str for x in ['запрос', 'договор']):
        return 0.0

    # Очистка от символов
    price_str = re.sub(r'[^\d,.]', '', price_str).replace(',', '.')
    return float(price_str)
```

---

### 3. SqlRepository - Работа с БД

**Назначение:** Реализация интерфейса IRepository через SQLAlchemy

**Особенности:**

**Индексы для производительности:**
```python
def _ensure_indexes(self):
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_vendor_article ON Total_Price(Vendor, Part_Num)"
    ))
```

**Обработка legacy данных (миграция ОВЕН → OWEN):**
```python
def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
    vendor_variants = [vendor_normalized]
    if vendor_normalized == 'OWEN':
        vendor_variants.append('ОВЕН')  # Старое название

    query = f"WHERE Vendor IN ({placeholders})"
```

**Нормализация при чтении:**
```python
article_normalized = self.data_normalizer.normalize_article(row[1], vendor)
unit_normalized = self.data_normalizer.normalize_unit(row[4])
```

---

### 4. Telegram Bot Handlers

**Назначение:** Интерфейс управления через Telegram

**Основные команды:**
- `/start` - приветствие
- `/sync` - выбор вендора для синхронизации
- `/sync_all` - синхронизация всех вендоров
- `/check` - проверка изменений без обновления БД
- `/status` - статистика последних синхронизаций
- `/debug` - отладочная информация

**Асинхронность:**
```python
async def sync_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vendors = ['KEAZ', 'OWEN', 'EKF', 'IEK', 'DKC', 'CHINT']

    for vendor in vendors:
        # Запуск синхронизации через subprocess
        result = subprocess.run(
            [settings.PYTHON_PATH, 'main.py', vendor],
            timeout=600,
            capture_output=True
        )

        # Парсинг вывода и отправка результата
        await context.bot.send_message(text=report)
```

**Интересное решение:** Выполнение синхронизации через subprocess, а не напрямую
- Изоляция процессов
- Защита от блокировки бота при долгих операциях
- Логирование в отдельные потоки

---

### 5. VendorRegistry - Реестр поставщиков

**Назначение:** Централизованная конфигурация всех вендоров

**Пример конфигурации:**
```python
'IEK': VendorConfig(
    name='IEK',
    downloader_class=IekDownloader,
    downloader_params={'download_dir': self.download_dir},
    parser_config={
        'engine': 'openpyxl',
        'columns': {
            'article': 'Артикул',
            'description': 'Наименование',
            'price': 'Базовая цена с НДС',
            'units': 'Ед.'
        }
    }
)
```

**Фабричные методы:**
```python
def create_downloader(self, vendor: str):
    config = self._vendors[vendor]
    return config.downloader_class(**config.downloader_params)

def create_parser(self, vendor: str):
    config = self._vendors[vendor]
    return ExcelParser(config.parser_config, self.normalizer)
```

---

## Качество кода

### Сильные стороны

**1. Архитектура**
- Чистое разделение слоев (domain, adapters, infrastructure)
- SOLID принципы соблюдены
- Высокая модульность

**2. Логирование**
```python
logger.info(f"📊 Распарсено {len(new_items)} позиций")
logger.warning(f"⚠️ Не найден для пометки disappeared: {article}")
logger.error(f"❌ Ошибка синхронизации {vendor}: {e}", exc_info=True)
```

**3. Обработка ошибок**
```python
try:
    service = create_sync_service(vendor)
    result = service.sync_vendor(vendor)
except Exception as e:
    logger.error(f"Критическая ошибка: {e}", exc_info=True)
    sys.exit(1)
```

**4. Типизация**
```python
def sync_vendor(self, vendor: str) -> SyncResult:
    ...

def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
    ...
```

### Области для улучшения

**1. Отсутствие тестов**
- Нет unit-тестов
- Нет integration-тестов
- Критично для бизнес-логики синхронизации

**Рекомендация:**
```python
# tests/domain/test_sync_service.py
def test_sync_vendor_adds_new_items():
    mock_downloader = Mock(spec=IDownloader)
    mock_parser = Mock(spec=IParser)
    mock_repository = Mock(spec=IRepository)

    service = SyncService(mock_downloader, mock_parser, mock_repository)
    result = service.sync_vendor('TEST')

    assert result.success
    mock_repository.add_items.assert_called_once()
```

**2. Конфигурация линтеров**
- Отсутствует `.flake8`, `pyproject.toml`
- Нет pre-commit hooks

**Рекомендация:**
```toml
# pyproject.toml
[tool.black]
line-length = 100

[tool.isort]
profile = "black"

[tool.mypy]
python_version = "3.11"
strict = true
```

**3. Документация**
- В коде есть docstrings, но не везде
- Отсутствует API документация

**Рекомендация:**
```python
def sync_vendor(self, vendor: str) -> SyncResult:
    """
    Синхронизирует прайс-лист вендора.

    Args:
        vendor: Название вендора (KEAZ, IEK, и т.д.)

    Returns:
        SyncResult: Объект с результатами синхронизации

    Raises:
        ValueError: Если вендор не найден в реестре
        DownloadError: Если не удалось загрузить файл
    """
```

**4. Безопасность**

Хардкод credentials в [config/settings.py](config/settings.py:16-17):
```python
DKC_LOGIN = os.getenv('DKC_LOGIN', 'branx')  # ← Дефолтное значение
DKC_PASSWORD = os.getenv('DKC_PASSWORD', '11051987')  # ← Не должно быть
```

**Рекомендация:**
```python
DKC_LOGIN = os.getenv('DKC_LOGIN')
if not DKC_LOGIN:
    raise ValueError("DKC_LOGIN не установлен в .env")
```

**5. Производительность**

Последовательная обработка в цикле:
```python
for article in disappeared_articles:
    query = "UPDATE ... WHERE Part_Num = :article"
    session.execute(query, {'article': article})
```

**Рекомендация:**
```python
# Batch update
query = "UPDATE ... WHERE Part_Num IN :articles"
session.execute(query, {'articles': tuple(disappeared_articles)})
```

---

## Паттерны и best practices

### 1. Нормализация данных

**Проблема:** Разные вендоры пишут артикулы по-разному:
- `"АВВ 123"` vs `"ABB123"` vs `"abb-123"`

**Решение:**
```python
class ArticleNormalizer:
    RULES = {
        'KEAZ': lambda x: re.sub(r'\s+', '', x.strip().upper()),
        'IEK': lambda x: re.sub(r'\s+', '', x.strip().upper()),
    }

    def normalize(self, article: str, vendor: str) -> str:
        return self.RULES.get(vendor, self._default_rule)(article)
```

### 2. Handling Special Cases (Заказные позиции)

**Проблема:** Позиции "под запрос" имеют цену `None`, `"запрос"`, `"договор"`

**Решение:**
```python
def clean_price_value(self, price_val: Any) -> float:
    if self.is_price_on_request(str(price_val)):
        return 0.0  # Учитываем, но с нулевой ценой
    return float(price_str)

# При синхронизации
new_items_with_price = [item for item in new_items if item.price > 0]
# Для добавления/обновления используем только позиции с ценой
# Но для определения "исчезнувших" учитываем все позиции
```

### 3. Configuration as Code

```python
# Вместо if-else для каждого вендора
if vendor == 'KEAZ':
    url = 'https://...'
    columns = {...}
elif vendor == 'IEK':
    ...

# Используем конфигурацию
VENDORS = {
    'KEAZ': VendorConfig(url='...', columns={...}),
    'IEK': VendorConfig(url='...', columns={...})
}
```

### 4. Defensive Programming

```python
# Проверка на None/пустоту
stdout = result.stdout or ""
stderr = result.stderr or ""

# Валидация в __post_init__
@dataclass
class PriceItem:
    def __post_init__(self):
        if not self.vendor:
            raise ValueError("Vendor cannot be empty")
        if self.price < 0:
            raise ValueError("Price cannot be negative")
```

### 5. Resource Cleanup

```python
# Context managers для сессий БД
with self.SessionLocal() as session:
    result = session.execute(query)
    session.commit()
# Автоматическое закрытие сессии

# Try-finally для результатов
def sync_vendor(self, vendor: str) -> SyncResult:
    result = SyncResult(vendor=vendor)
    try:
        ...
    except Exception as e:
        result.error_message = str(e)
    finally:
        result.finished_at = datetime.now()
    return result
```

---

## Инфраструктура разработки

### Переменные окружения (.env)

```bash
BOT_TOKEN=your_telegram_bot_token
DATABASE_URL=sqlite:///./prices.db
DKC_LOGIN=your_login
DKC_PASSWORD=your_password
PROXY_URL=http://proxy:8080  # Опционально
```

### Скрипты запуска

**CLI синхронизация:**
```bash
# Один вендор
python main.py KEAZ

# Все вендоры
python main.py
```

**Telegram бот:**
```bash
python -m bot.main
```

### Структура БД

```sql
CREATE TABLE Total_Price (
    id INTEGER PRIMARY KEY,
    Vendor TEXT NOT NULL,
    Part_Num TEXT NOT NULL,
    Descr TEXT,
    Price REAL,
    Units TEXT,
    Storage TEXT,
    Status TEXT DEFAULT 'active',  -- 'new', 'price_changed', 'disappeared'
    updated_at TEXT,
    UNIQUE(Vendor, Part_Num)
);

CREATE INDEX idx_vendor_article ON Total_Price(Vendor, Part_Num);
CREATE INDEX idx_vendor ON Total_Price(Vendor);
```

### Логирование

```python
# Конфигурация в main.py
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/sync.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
```

**Логи пишутся в:**
- `logs/sync.log` - файл (persistent)
- `stdout` - консоль (для мониторинга)

---

## Выводы и рекомендации

### Сильные стороны проекта

1. **Превосходная архитектура**
   - Clean Architecture + DDD
   - Высокая модульность и расширяемость
   - Правильные абстракции

2. **Решение реальной бизнес-задачи**
   - Автоматизация рутинной работы
   - Отслеживание изменений цен
   - Удобный интерфейс через Telegram

3. **Обработка сложных случаев**
   - Разнообразные форматы Excel
   - Нормализация данных
   - Миграция legacy данных

4. **Качественное логирование**
   - Детальные логи на каждом этапе
   - Удобная отладка через `/debug`

### Рекомендации по улучшению

#### Критичные (высокий приоритет)

**1. Добавить тесты**
```python
# Минимальное покрытие
tests/
├── domain/
│   ├── test_sync_service.py
│   └── test_price_item.py
├── adapters/
│   ├── test_excel_parser.py
│   └── test_sql_repository.py
└── conftest.py
```

**2. Убрать хардкод credentials**
```python
# config/settings.py
DKC_LOGIN = os.getenv('DKC_LOGIN')
if not DKC_LOGIN:
    raise EnvironmentError("DKC_LOGIN must be set")
```

**3. Добавить обработку edge cases**
```python
# Таймауты для загрузки
requests.get(url, timeout=30)

# Проверка размера файла
if file_size > 100_000_000:  # 100MB
    raise FileTooLargeError()
```

#### Важные (средний приоритет)

**4. Настроить линтеры и форматтеры**
```bash
pip install black isort flake8 mypy
```

```toml
# pyproject.toml
[tool.black]
line-length = 100
target-version = ['py311']

[tool.mypy]
strict = true
ignore_missing_imports = true
```

**5. Добавить pre-commit hooks**
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.7.0
    hooks:
      - id: black
  - repo: https://github.com/pycqa/isort
    rev: 5.12.0
    hooks:
      - id: isort
```

**6. Улучшить производительность БД**
```python
# Batch операции вместо циклов
def mark_as_disappeared(self, vendor: str, articles: List[str]) -> int:
    query = """
        UPDATE Total_Price
        SET Status = 'disappeared'
        WHERE Vendor = :vendor AND Part_Num IN :articles
    """
    session.execute(query, {'vendor': vendor, 'articles': tuple(articles)})
```

#### Желательные (низкий приоритет)

**7. Добавить мониторинг**
```python
# Prometheus metrics
from prometheus_client import Counter, Histogram

sync_duration = Histogram('sync_duration_seconds', 'Время синхронизации')
sync_errors = Counter('sync_errors_total', 'Количество ошибок')
```

**8. Миграции БД**
```python
# Alembic для версионирования схемы БД
alembic init migrations
alembic revision -m "Initial schema"
```

**9. Async/await для I/O операций**
```python
# asyncio для параллельной загрузки
async def download_all_vendors():
    tasks = [download_vendor(v) for v in vendors]
    await asyncio.gather(*tasks)
```

**10. CI/CD**
```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run tests
        run: pytest
```

### Уровень сложности

**Middle-friendly аспекты:**
- Хорошо структурированный код
- Понятные абстракции
- Обширное логирование
- Примеры паттернов проектирования

**Senior-friendly аспекты:**
- Архитектурные решения (DDD, Clean Architecture)
- Обработка сложных edge cases
- Нормализация и миграция данных
- Оптимизация производительности

### Итоговая оценка

**Оценка:** 8/10

**Обоснование:**
- Превосходная архитектура (+2)
- Решение реальной задачи (+2)
- Качественный код (+2)
- Хорошее логирование (+1)
- Отсутствие тестов (-2)
- Отсутствие линтеров (-1)

Проект демонстрирует профессиональный подход к разработке с упором на архитектуру и поддерживаемость. Основная область для улучшения - добавление автоматизированного тестирования.

---

**Составлено:** Claude Sonnet 4.5
**Дата:** 2026-01-07
