# Архитектура проекта Price Sync Bot

## Общая схема

```
┌─────────────────────────────────────────────────────────────┐
│                     PRESENTATION LAYER                       │
│  ┌────────────────┐              ┌────────────────────────┐ │
│  │  Telegram Bot  │              │   CLI (main.py)        │ │
│  │  (bot/main.py) │              │                        │ │
│  └────────┬───────┘              └───────────┬────────────┘ │
└───────────┼──────────────────────────────────┼──────────────┘
            │                                  │
            │         ┌───────────────────────┘
            │         │
┌───────────▼─────────▼─────────────────────────────────────┐
│                   APPLICATION LAYER                        │
│  ┌───────────────────────────────────────────────────┐    │
│  │          SyncService (Orchestrator)               │    │
│  │  • Координирует загрузку, парсинг, синхронизацию  │    │
│  │  • Применяет бизнес-правила                       │    │
│  └───────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────┘
            │
            ├──────────────┬─────────────┬─────────────┐
            │              │             │             │
┌───────────▼──────────────▼─────────────▼─────────────▼─────┐
│                     DOMAIN LAYER                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │   Entities   │  │  Interfaces  │  │ Domain Services  │ │
│  │  PriceItem   │  │  IDownloader │  │  ReportService   │ │
│  │  SyncResult  │  │  IParser     │  │  DataNormalizer  │ │
│  └──────────────┘  │  IRepository │  └──────────────────┘ │
│                    └──────────────┘                        │
└────────────────────────────────────────────────────────────┘
            │
            │ (implements)
            │
┌───────────▼────────────────────────────────────────────────┐
│                   INFRASTRUCTURE LAYER                      │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Downloaders │  │   Parsers    │  │  Repositories    │  │
│  │  - Simple   │  │ ExcelParser  │  │ SqlRepository    │  │
│  │  - Auth     │  │              │  │ (SQLAlchemy)     │  │
│  │  - IEK      │  │              │  │                  │  │
│  │  - DKC      │  │              │  │                  │  │
│  └─────────────┘  └──────────────┘  └──────────────────┘  │
└────────────────────────────────────────────────────────────┘
            │              │              │
            ▼              ▼              ▼
      ┌────────┐      ┌────────┐     ┌────────┐
      │  HTTP  │      │ Excel  │     │ SQLite │
      │ APIs   │      │ Files  │     │   DB   │
      └────────┘      └────────┘     └────────┘
```

## Принципы архитектуры

### 1. Clean Architecture

Зависимости направлены внутрь (к домену):

```
Infrastructure → Domain ← Application ← Presentation
```

**Правило:** Внутренние слои не знают о внешних
- Domain не зависит от БД, HTTP, Telegram
- Легко заменить SQLite на PostgreSQL
- Легко заменить Telegram на веб-интерфейс

### 2. Dependency Inversion Principle (DIP)

Высокоуровневые модули не зависят от низкоуровневых. Оба зависят от абстракций.

**Пример:**

```python
# ❌ Плохо: прямая зависимость
class SyncService:
    def __init__(self):
        self.repository = SqlRepository()  # Жесткая связь

# ✅ Хорошо: зависимость от интерфейса
class SyncService:
    def __init__(self, repository: IRepository):
        self.repository = repository  # Любая реализация IRepository
```

### 3. Single Responsibility Principle (SRP)

Каждый класс имеет одну ответственность:

- `IekDownloader` - только загрузка файлов IEK
- `ExcelParser` - только парсинг Excel
- `SqlRepository` - только работа с БД
- `SyncService` - только оркестрация процесса

### 4. Open/Closed Principle (OCP)

Открыт для расширения, закрыт для модификации.

**Добавление нового вендора:**
```python
# Не нужно менять существующий код!
# Просто добавляем конфигурацию:
'NEW_VENDOR': VendorConfig(
    downloader_class=SimpleHttpDownloader,
    downloader_params={'url': '...'},
    parser_config={...}
)
```

---

## Слои архитектуры

### Presentation Layer (Представление)

**Компоненты:**
- `bot/main.py` - Telegram бот
- `bot/handlers.py` - Обработчики команд
- `main.py` - CLI интерфейс

**Ответственность:**
- Прием команд от пользователя
- Валидация ввода
- Форматирование вывода

**Не делает:**
- Бизнес-логику
- Работу с БД
- Загрузку файлов

### Application Layer (Приложение)

**Компоненты:**
- `domain/services/sync_service.py`

**Ответственность:**
- Оркестрация use cases
- Координация между domain и infrastructure
- Обработка транзакций

**Пример use case:**
```python
def sync_vendor(self, vendor: str) -> SyncResult:
    # 1. Загрузка (infrastructure)
    file_path = self.downloader.download(vendor)

    # 2. Парсинг (infrastructure)
    new_items = self.parser.parse(file_path, vendor)

    # 3. Бизнес-логика (domain)
    current_items = self.repository.get_items_by_vendor(vendor)
    to_add, to_update, disappeared = self._analyze_changes(...)

    # 4. Сохранение (infrastructure)
    self.repository.add_items(to_add)

    return result
```

### Domain Layer (Домен)

**Компоненты:**
- `domain/entities/` - Сущности
- `domain/interfaces/` - Интерфейсы (порты)
- `domain/services/` - Доменные сервисы

**Ответственность:**
- Бизнес-правила
- Доменная логика
- Валидация бизнес-ограничений

**Пример:**
```python
@dataclass
class PriceItem:
    price: Decimal

    def has_price_changed(self, other: 'PriceItem', threshold: float) -> bool:
        # Бизнес-правило: изменение менее threshold% не учитывается
        return abs(float(self.price) - float(other.price)) > threshold
```

### Infrastructure Layer (Инфраструктура)

**Компоненты:**
- `adapters/downloaders/` - Загрузчики
- `adapters/parsers/` - Парсеры
- `adapters/database/` - Репозитории
- `vendors/registry.py` - Конфигурация

**Ответственность:**
- Реализация интерфейсов домена
- Работа с внешними системами
- I/O операции

---

## Поток данных

### Сценарий: Синхронизация вендора KEAZ

```
1. Пользователь → Telegram Bot
   /sync → выбирает "KEAZ"

2. Bot → subprocess → main.py KEAZ

3. main.py:
   ├─ VendorRegistry.create_downloader('KEAZ')
   │  └─ SimpleHttpDownloader
   │
   ├─ VendorRegistry.create_parser('KEAZ')
   │  └─ ExcelParser
   │
   ├─ SqlRepository('sqlite:///prices.db')
   │
   └─ SyncService(downloader, parser, repository)

4. SyncService.sync_vendor('KEAZ'):
   │
   ├─ downloader.download('KEAZ')
   │  └─ GET https://files.keaz.ru/ftp/keaz.xls
   │      → price_files/keaz_2026-01-07.xls
   │
   ├─ parser.parse(file_path, 'KEAZ')
   │  ├─ pandas.read_excel()
   │  ├─ Найти заголовки
   │  ├─ Нормализация данных
   │  └─ → List[PriceItem]
   │
   ├─ repository.get_items_by_vendor('KEAZ')
   │  └─ SELECT * FROM Total_Price WHERE Vendor='KEAZ'
   │      → List[PriceItem] (текущие данные)
   │
   ├─ Сравнение (бизнес-логика)
   │  ├─ Новые = new_articles - current_articles
   │  ├─ Обновленные = has_price_changed()
   │  └─ Исчезнувшие = current_articles - new_articles
   │
   ├─ repository.add_items(new_items)
   │  └─ INSERT INTO Total_Price ...
   │
   ├─ repository.update_items(updated_items)
   │  └─ UPDATE Total_Price SET Price=...
   │
   ├─ repository.mark_as_disappeared(disappeared)
   │  └─ UPDATE Total_Price SET Status='disappeared'
   │
   └─ → SyncResult(
         success=True,
         total_items=31986,
         new_items=12,
         updated_items=143,
         disappeared_items=5
      )

5. main.py → stdout:
   "KEAZ: total=31986, new=12, updated=143, disappeared=5, time=5.2s"

6. Bot парсит вывод → отправляет сообщение пользователю:
   "✅ KEAZ готово!
    📦 Всего: 31986
    ➕ Новых: 12
    🔄 Обновлено: 143
    👻 Исчезло: 5"
```

---

## Dependency Injection

### Ручная инжекция зависимостей

Проект использует **ручную инжекцию** без DI-фреймворка:

```python
def create_sync_service(vendor: str) -> SyncService:
    # 1. Создаем инфраструктурные компоненты
    normalizer = ArticleNormalizer()
    registry = VendorRegistry(settings.PRICE_FILES_DIR, normalizer)
    repository = SqlRepository(settings.DATABASE_URL)

    # 2. Создаем вендор-специфичные компоненты
    downloader = registry.create_downloader(vendor)
    parser = registry.create_parser(vendor)

    # 3. Инжектим зависимости в сервис
    service = SyncService(
        downloader=downloader,
        parser=parser,
        repository=repository,
        price_change_threshold=settings.PRICE_CHANGE_THRESHOLD
    )

    return service
```

**Преимущества:**
- Простота (нет магии фреймворков)
- Явные зависимости
- Легко понять и отладить

**Недостатки:**
- Много boilerplate кода
- Сложнее в больших проектах

### Тестирование через моки

```python
# Легко заменить реализацию для тестов
def test_sync_service():
    mock_downloader = Mock(spec=IDownloader)
    mock_downloader.download.return_value = Path('test.xlsx')

    mock_parser = Mock(spec=IParser)
    mock_parser.parse.return_value = [PriceItem(...)]

    mock_repository = Mock(spec=IRepository)

    service = SyncService(
        downloader=mock_downloader,
        parser=mock_parser,
        repository=mock_repository
    )

    result = service.sync_vendor('TEST')

    assert result.success
```

---

## Паттерны проектирования

### 1. Repository Pattern

**Проблема:** Изоляция domain от деталей хранения данных

**Решение:**
```python
# Интерфейс (порт)
class IRepository(ABC):
    @abstractmethod
    def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
        pass

# Реализация (адаптер)
class SqlRepository(IRepository):
    def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
        # SQL реализация
        ...

# Будущая реализация
class MongoRepository(IRepository):
    def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
        # MongoDB реализация
        ...
```

### 2. Strategy Pattern

**Проблема:** Разные алгоритмы загрузки для разных вендоров

**Решение:**
```python
# Интерфейс стратегии
class IDownloader(ABC):
    @abstractmethod
    def download(self, vendor: str) -> Path:
        pass

# Конкретные стратегии
class SimpleHttpDownloader(IDownloader):
    def download(self, vendor: str) -> Path:
        # Простой GET запрос
        ...

class IekDownloader(IDownloader):
    def download(self, vendor: str) -> Path:
        # Selenium для JavaScript
        ...

class DkcDownloader(IDownloader):
    def download(self, vendor: str) -> Path:
        # Авторизация + POST
        ...

# Выбор стратегии
downloader = registry.create_downloader(vendor)
```

### 3. Factory Pattern

**Проблема:** Создание объектов с разными конфигурациями

**Решение:**
```python
class VendorRegistry:
    def create_downloader(self, vendor: str) -> IDownloader:
        config = self._vendors[vendor]
        # Фабрика создает нужный класс
        return config.downloader_class(**config.downloader_params)

    def create_parser(self, vendor: str) -> IParser:
        config = self._vendors[vendor]
        return ExcelParser(config.parser_config, self.normalizer)
```

### 4. Value Object Pattern

**Проблема:** Бизнес-логика размазана по сервисам

**Решение:**
```python
@dataclass
class PriceItem:
    vendor: str
    article: str
    price: Decimal

    def has_price_changed(self, other: 'PriceItem', threshold: float) -> bool:
        # Логика сравнения инкапсулирована в объекте
        return abs(float(self.price) - float(other.price)) > threshold

    @property
    def unique_key(self) -> str:
        return f"{self.vendor}_{self.article}"
```

### 5. Data Transfer Object (DTO)

**Проблема:** Передача результатов между слоями

**Решение:**
```python
@dataclass
class SyncResult:
    vendor: str
    success: bool
    total_items: int = 0
    new_items: int = 0
    updated_items: int = 0
    disappeared_items: int = 0
    error_message: str = ""
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None

    @property
    def duration(self) -> float:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0
```

---

## Масштабируемость

### Добавление нового вендора

**Шаг 1:** Добавить конфигурацию в `vendors/registry.py`:
```python
'NEW_VENDOR': VendorConfig(
    name='NEW_VENDOR',
    downloader_class=SimpleHttpDownloader,
    downloader_params={
        'download_dir': self.download_dir,
        'url': 'https://new-vendor.com/price.xlsx'
    },
    parser_config={
        'engine': 'openpyxl',
        'columns': {
            'article': 'Артикул',
            'description': 'Название',
            'price': 'Цена',
            'units': 'Ед.'
        }
    }
)
```

**Шаг 2:** Добавить в список вендоров в боте:
```python
# bot/handlers.py
vendors = ['KEAZ', 'OWEN', 'EKF', 'IEK', 'DKC', 'CHINT', 'NEW_VENDOR']
```

**Готово!** Новый вендор работает без изменения кода.

### Добавление нового типа загрузки

Если вендор требует специальную логику:

```python
# adapters/downloaders/special_downloader.py
class SpecialDownloader(BaseDownloader):
    def download(self, vendor: str) -> Path:
        # Уникальная логика
        ...

# vendors/registry.py
'SPECIAL_VENDOR': VendorConfig(
    downloader_class=SpecialDownloader,  # ← Новый класс
    ...
)
```

### Замена БД на PostgreSQL

```python
# config/settings.py
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:pass@localhost/prices')

# Всё остальное работает без изменений!
# SqlRepository использует SQLAlchemy, который поддерживает PostgreSQL
```

---

## Безопасность

### 1. Изоляция процессов

Telegram бот запускает синхронизацию через subprocess:
```python
result = subprocess.run(
    [settings.PYTHON_PATH, 'main.py', vendor],
    timeout=600,
    capture_output=True
)
```

**Преимущества:**
- Падение синхронизации не роняет бота
- Изоляция памяти
- Контроль таймаутов

### 2. SQL Injection Protection

SQLAlchemy параметризованные запросы:
```python
# ✅ Безопасно
query = text("SELECT * FROM Total_Price WHERE Vendor = :vendor")
session.execute(query, {"vendor": vendor})

# ❌ Опасно (в проекте не используется)
query = f"SELECT * FROM Total_Price WHERE Vendor = '{vendor}'"
```

### 3. Валидация данных

```python
@dataclass
class PriceItem:
    def __post_init__(self):
        if not self.vendor:
            raise ValueError("Vendor cannot be empty")
        if self.price < 0:
            raise ValueError("Price cannot be negative")
```

---

## Производительность

### 1. Индексы БД

```python
session.execute(text(
    "CREATE INDEX IF NOT EXISTS idx_vendor_article ON Total_Price(Vendor, Part_Num)"
))
```

**Результат:** Поиск по `(Vendor, Part_Num)` за O(log n) вместо O(n)

### 2. Batch операции

```python
# Вставка сразу всех items
data = [{'vendor': item.vendor, ...} for item in items]
session.execute(query, data)  # Один запрос вместо N
```

### 3. Lazy loading

```python
# pandas читает файл chunk-by-chunk
for chunk in pd.read_excel(file, chunksize=1000):
    process(chunk)
```

### Узкие места

**Проблема 1:** Последовательная обработка вендоров
```python
# Текущая реализация
for vendor in vendors:
    result = sync_one_vendor(vendor)  # Последовательно
```

**Решение:** Параллельная обработка
```python
# Рекомендация
import asyncio

async def sync_all_vendors():
    tasks = [sync_vendor_async(v) for v in vendors]
    results = await asyncio.gather(*tasks)
```

**Проблема 2:** Цикл обновлений в БД
```python
# bot/handlers.py mark_as_disappeared
for article in articles:
    query = "UPDATE ... WHERE Part_Num = :article"
    session.execute(query, {'article': article})
```

**Решение:** Batch update
```python
query = "UPDATE ... WHERE Part_Num IN :articles"
session.execute(query, {'articles': tuple(articles)})
```

---

## Диаграмма классов (ключевые компоненты)

```
┌────────────────────────────────────────────────────────┐
│                     SyncService                        │
├────────────────────────────────────────────────────────┤
│ - downloader: IDownloader                              │
│ - parser: IParser                                      │
│ - repository: IRepository                              │
│ - price_change_threshold: float                        │
├────────────────────────────────────────────────────────┤
│ + sync_vendor(vendor: str) -> SyncResult               │
│ + check_price_changes(vendor: str) -> ComparisonResult │
└────────────────────────────────────────────────────────┘
                │           │           │
       ┌────────┘           │           └────────┐
       │                    │                    │
       ▼                    ▼                    ▼
┌──────────────┐   ┌────────────────┐   ┌────────────────┐
│ IDownloader  │   │    IParser     │   │  IRepository   │
├──────────────┤   ├────────────────┤   ├────────────────┤
│ <<interface>>│   │  <<interface>> │   │ <<interface>>  │
├──────────────┤   ├────────────────┤   ├────────────────┤
│ + download() │   │ + parse()      │   │ + get_items()  │
└──────────────┘   └────────────────┘   │ + add_items()  │
       △                    △            │ + update()     │
       │                    │            └────────────────┘
       │                    │                    △
    ┌──┴──┬──────┬──────┐  │                    │
    │     │      │      │  │                    │
┌───┴──┐ ┌┴────┐┌┴───┐ │  │            ┌───────┴────────┐
│Simple│ │Auth ││IEK │ │  │            │ SqlRepository  │
│HTTP  │ │HTTP ││Down││  │            ├────────────────┤
└──────┘ └─────┘└────┘ │  │            │ - engine       │
                       │  │            │ - SessionLocal │
                  ┌────┴┐ │            ├────────────────┤
                  │DKC  │ │            │ + get_items()  │
                  │Down │ │            │ + add_items()  │
                  └─────┘ │            │ + update()     │
                          │            └────────────────┘
                  ┌───────┴──────┐
                  │ ExcelParser  │
                  ├──────────────┤
                  │ - config     │
                  │ - normalizer │
                  ├──────────────┤
                  │ + parse()    │
                  └──────────────┘
```

---

**Составлено:** Claude Sonnet 4.5
**Дата:** 2026-01-07
