# Руководство разработчика Price Sync Bot

## Начало работы

### Настройка окружения разработки

**1. Клонирование и установка:**

```bash
git clone <repository-url>
cd price_sync_bot
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows

pip install -r requirements.txt
```

**2. Установка инструментов разработки:**

```bash
pip install black isort flake8 mypy pytest pytest-cov
```

**3. Настройка .env для разработки:**

```bash
# .env.dev
BOT_TOKEN=test_bot_token
DATABASE_URL=sqlite:///./test_prices.db
DKC_LOGIN=test_login
DKC_PASSWORD=test_password
```

**4. Инициализация тестовой БД:**

```bash
python init_db_universal.py
```

---

## Архитектура проекта

### Слои приложения

```
┌─────────────────────────────────────────┐
│         Presentation Layer              │
│     (bot/, main.py)                     │
└─────────────┬───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│         Application Layer               │
│     (domain/services/)                  │
└─────────────┬───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│           Domain Layer                  │
│     (domain/entities/,                  │
│      domain/interfaces/)                │
└─────────────┬───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│      Infrastructure Layer               │
│     (adapters/)                         │
└─────────────────────────────────────────┘
```

### Принципы проектирования

**1. Dependency Inversion Principle**

Domain определяет интерфейсы, infrastructure их реализует:

```python
# domain/interfaces/repository.py (порт)
class IRepository(ABC):
    @abstractmethod
    def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
        pass

# adapters/database/sql_repository.py (адаптер)
class SqlRepository(IRepository):
    def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
        # SQL реализация
```

**2. Single Responsibility**

Каждый модуль отвечает за одну задачу:
- `IekDownloader` - только загрузка IEK
- `ExcelParser` - только парсинг Excel
- `SyncService` - только оркестрация

**3. Open/Closed**

Расширяем через конфигурацию, а не модификацию кода:

```python
# Добавление нового вендора - только конфигурация
'NEW_VENDOR': VendorConfig(...)
```

---

## Добавление функционала

### Добавление нового вендора

**Шаг 1: Определите требования**

- URL прайс-листа
- Формат файла (Excel/CSV)
- Структура колонок
- Требования к авторизации

**Шаг 2: Выберите/создайте загрузчик**

**Простой HTTP:**
```python
# Используем SimpleHttpDownloader
'NEW_VENDOR': VendorConfig(
    downloader_class=SimpleHttpDownloader,
    downloader_params={
        'download_dir': self.download_dir,
        'url': 'https://vendor.com/price.xlsx'
    }
)
```

**Требуется авторизация:**
```python
# adapters/downloaders/new_vendor_downloader.py
class NewVendorDownloader(BaseDownloader):
    def download(self, vendor: str) -> Path:
        session = requests.Session()

        # Авторизация
        login_data = {'user': '...', 'password': '...'}
        session.post('https://vendor.com/login', data=login_data)

        # Загрузка файла
        response = session.get('https://vendor.com/price.xlsx')

        file_path = self.download_dir / f"{vendor.lower()}_{datetime.now():%Y%m%d}.xlsx"
        file_path.write_bytes(response.content)

        return file_path
```

**Шаг 3: Настройте парсер**

```python
# vendors/registry.py
'NEW_VENDOR': VendorConfig(
    name='NEW_VENDOR',
    downloader_class=NewVendorDownloader,
    downloader_params={'download_dir': self.download_dir},
    parser_config={
        'engine': 'openpyxl',  # или 'xlrd' для .xls
        'columns': {
            'article': 'Артикул',       # Название колонки в Excel
            'description': 'Название',
            'price': 'Цена с НДС',
            'units': 'Ед.изм.'
        }
    }
)
```

**Шаг 4: Добавьте в список вендоров бота**

```python
# bot/handlers.py
vendors = ['KEAZ', 'OWEN', 'EKF', 'IEK', 'DKC', 'CHINT', 'NEW_VENDOR']
```

**Шаг 5: Тестирование**

```bash
# Тест загрузки и парсинга
python main.py NEW_VENDOR

# Проверка через бота
/sync → NEW_VENDOR
```

### Добавление новой команды в бот

**Шаг 1: Создайте обработчик**

```python
# bot/handlers.py
async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /export - экспорт данных вендора"""
    vendors = ['KEAZ', 'OWEN', 'EKF', 'IEK', 'DKC', 'CHINT']

    keyboard = [
        [InlineKeyboardButton(f"📊 {v}", callback_data=f"export_{v}")]
        for v in vendors
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📋 Выберите вендора для экспорта:",
        reply_markup=reply_markup
    )
```

**Шаг 2: Обработка callback**

```python
# bot/handlers.py
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith('export_'):
        vendor = query.data.replace('export_', '')
        await export_vendor_data(query, vendor)

    # ... остальные обработчики
```

**Шаг 3: Реализация логики**

```python
async def export_vendor_data(query, vendor: str):
    """Экспорт данных вендора в Excel"""
    from adapters.database.sql_repository import SqlRepository
    import pandas as pd

    repository = SqlRepository(settings.DATABASE_URL)
    items = repository.get_items_by_vendor(vendor)

    # Конвертация в DataFrame
    data = [{
        'Артикул': item.article,
        'Название': item.description,
        'Цена': float(item.price),
        'Ед.': item.units
    } for item in items]

    df = pd.DataFrame(data)

    # Сохранение в Excel
    file_path = f'exports/{vendor}_{datetime.now():%Y%m%d}.xlsx'
    df.to_excel(file_path, index=False)

    # Отправка файла пользователю
    await query.message.reply_document(
        document=open(file_path, 'rb'),
        filename=f'{vendor}_export.xlsx'
    )
```

**Шаг 4: Регистрация команды**

```python
# bot/main.py
app.add_handler(CommandHandler("export", export_command))
```

**Шаг 5: Обновите справку**

```python
# bot/handlers.py
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """📚 Команды:

/sync - Синхронизировать вендора
/sync_all - Синхронизировать всех
/check - Проверить актуальность прайсов
/export - Экспортировать данные вендора  ← НОВАЯ
/status - Статус синхронизаций
/debug - Показать ошибки
/help - Справка
"""
    await update.message.reply_text(text)
```

### Добавление нового парсера

**Пример: Парсинг CSV файлов**

```python
# adapters/parsers/csv_parser.py
import csv
from pathlib import Path
from typing import List
from domain.entities.price_item import PriceItem
from domain.interfaces.parser import IParser

class CsvParser(IParser):
    """Парсер CSV файлов"""

    def __init__(self, config: dict, normalizer):
        self.config = config
        self.normalizer = normalizer

    def parse(self, file_path: Path, vendor: str) -> List[PriceItem]:
        items = []
        columns = self.config.get('columns', {})

        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)

            for row in reader:
                try:
                    item = PriceItem(
                        vendor=vendor,
                        article=self.normalizer.normalize(
                            row[columns['article']], vendor
                        ),
                        description=row.get(columns.get('description', ''), ''),
                        price=float(row[columns['price']]),
                        units=row.get(columns.get('units', ''), 'шт')
                    )
                    items.append(item)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Пропущена строка: {e}")
                    continue

        return items
```

**Использование:**

```python
# vendors/registry.py
'CSV_VENDOR': VendorConfig(
    parser_class=CsvParser,  # ← Новый парсер
    parser_config={
        'columns': {
            'article': 'SKU',
            'description': 'Product Name',
            'price': 'Price',
            'units': 'Unit'
        }
    }
)
```

---

## Тестирование

### Unit тесты

**Структура:**

```
tests/
├── domain/
│   ├── test_price_item.py
│   ├── test_sync_service.py
│   └── test_data_normalizer.py
├── adapters/
│   ├── test_excel_parser.py
│   └── test_sql_repository.py
└── conftest.py
```

**Пример: Тест сущности**

```python
# tests/domain/test_price_item.py
import pytest
from decimal import Decimal
from domain.entities.price_item import PriceItem

def test_price_item_creation():
    """Тест создания PriceItem"""
    item = PriceItem(
        vendor='KEAZ',
        article='VA47-29',
        description='Автомат 1P 16A C',
        price=Decimal('150.50'),
        units='шт'
    )

    assert item.vendor == 'KEAZ'
    assert item.price == Decimal('150.50')
    assert item.unique_key == 'KEAZ_VA47-29'

def test_price_item_validation():
    """Тест валидации PriceItem"""
    with pytest.raises(ValueError, match="Vendor cannot be empty"):
        PriceItem(vendor='', article='123', description='', price=100)

    with pytest.raises(ValueError, match="Price cannot be negative"):
        PriceItem(vendor='KEAZ', article='123', description='', price=-10)

def test_has_price_changed():
    """Тест определения изменения цены"""
    item1 = PriceItem('KEAZ', '123', 'Test', Decimal('100'), 'шт')
    item2 = PriceItem('KEAZ', '123', 'Test', Decimal('100.005'), 'шт')
    item3 = PriceItem('KEAZ', '123', 'Test', Decimal('105'), 'шт')

    # Изменение меньше порога
    assert not item1.has_price_changed(item2, threshold=0.01)

    # Изменение больше порога
    assert item1.has_price_changed(item3, threshold=0.01)
```

**Пример: Тест сервиса с моками**

```python
# tests/domain/test_sync_service.py
import pytest
from unittest.mock import Mock, MagicMock
from pathlib import Path
from domain.services.sync_service import SyncService
from domain.entities.price_item import PriceItem

@pytest.fixture
def sync_service():
    """Фикстура SyncService с моками"""
    mock_downloader = Mock()
    mock_parser = Mock()
    mock_repository = Mock()

    service = SyncService(
        downloader=mock_downloader,
        parser=mock_parser,
        repository=mock_repository,
        price_change_threshold=0.01
    )

    return service, mock_downloader, mock_parser, mock_repository

def test_sync_vendor_success(sync_service):
    """Тест успешной синхронизации"""
    service, downloader, parser, repository = sync_service

    # Настройка моков
    downloader.download.return_value = Path('test.xlsx')
    parser.parse.return_value = [
        PriceItem('KEAZ', '123', 'Item 1', 100, 'шт'),
        PriceItem('KEAZ', '456', 'Item 2', 200, 'шт')
    ]
    repository.get_items_by_vendor.return_value = []

    # Выполнение
    result = service.sync_vendor('KEAZ')

    # Проверки
    assert result.success
    assert result.total_items == 2
    downloader.download.assert_called_once_with('KEAZ')
    parser.parse.assert_called_once()
    repository.add_items.assert_called_once()

def test_sync_vendor_detects_new_items(sync_service):
    """Тест определения новых позиций"""
    service, downloader, parser, repository = sync_service

    downloader.download.return_value = Path('test.xlsx')

    # Новый прайс
    parser.parse.return_value = [
        PriceItem('KEAZ', '123', 'Item 1', 100, 'шт'),
        PriceItem('KEAZ', '789', 'Item 3', 300, 'шт')  # Новый
    ]

    # Текущая БД
    repository.get_items_by_vendor.return_value = [
        PriceItem('KEAZ', '123', 'Item 1', 100, 'шт')
    ]

    result = service.sync_vendor('KEAZ')

    # Проверка что вызван add_items с новой позицией
    assert repository.add_items.called
    added_items = repository.add_items.call_args[0][0]
    assert len(added_items) == 1
    assert added_items[0].article == '789'
```

**Запуск тестов:**

```bash
# Все тесты
pytest

# С coverage
pytest --cov=domain --cov=adapters --cov-report=html

# Только unit тесты
pytest tests/domain/

# Конкретный тест
pytest tests/domain/test_sync_service.py::test_sync_vendor_success -v
```

### Integration тесты

```python
# tests/integration/test_sync_flow.py
import pytest
from pathlib import Path
from config.settings import settings
from utils.normalizer import ArticleNormalizer
from vendors.registry import VendorRegistry
from adapters.database.sql_repository import SqlRepository

@pytest.fixture
def test_db():
    """Тестовая БД"""
    db_path = 'test_prices.db'
    repo = SqlRepository(f'sqlite:///{db_path}')

    # Создание таблиц
    # ...

    yield repo

    # Очистка
    Path(db_path).unlink(missing_ok=True)

def test_full_sync_flow(test_db):
    """Интеграционный тест полного цикла синхронизации"""
    normalizer = ArticleNormalizer()
    registry = VendorRegistry(Path('price_files'), normalizer)

    # Создание реальных компонентов
    downloader = registry.create_downloader('KEAZ')
    parser = registry.create_parser('KEAZ')

    service = SyncService(
        downloader=downloader,
        parser=parser,
        repository=test_db
    )

    # Выполнение синхронизации
    result = service.sync_vendor('KEAZ')

    # Проверки
    assert result.success
    assert result.total_items > 0

    # Проверка что данные в БД
    items = test_db.get_items_by_vendor('KEAZ')
    assert len(items) > 0
```

### E2E тесты бота

```python
# tests/e2e/test_bot_commands.py
import pytest
from telegram import Update
from telegram.ext import ContextTypes
from bot.handlers import start_command, sync_command

@pytest.mark.asyncio
async def test_start_command():
    """Тест команды /start"""
    # Мок Update и Context
    update = Mock(spec=Update)
    update.effective_user.first_name = 'Test User'
    update.message.reply_text = AsyncMock()

    context = Mock(spec=ContextTypes.DEFAULT_TYPE)

    # Выполнение
    await start_command(update, context)

    # Проверка
    update.message.reply_text.assert_called_once()
    call_args = update.message.reply_text.call_args[0][0]
    assert 'Привет, Test User' in call_args
```

---

## Отладка

### Логирование

**Настройка уровня логирования:**

```python
# main.py
import logging

# Для разработки
logging.basicConfig(level=logging.DEBUG)

# Детальное логирование конкретного модуля
logging.getLogger('adapters.parsers.excel_parser').setLevel(logging.DEBUG)
```

**Добавление логов:**

```python
import logging
logger = logging.getLogger(__name__)

def my_function():
    logger.debug("Начало выполнения")  # Подробности
    logger.info("Обработано 100 записей")  # Информация
    logger.warning("Пропущена запись")  # Предупреждение
    logger.error("Ошибка парсинга", exc_info=True)  # Ошибка со stacktrace
```

### Отладка через VSCode

**.vscode/launch.json:**

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python: Main",
            "type": "python",
            "request": "launch",
            "program": "${workspaceFolder}/main.py",
            "args": ["KEAZ"],
            "console": "integratedTerminal",
            "justMyCode": false,
            "env": {
                "PYTHONPATH": "${workspaceFolder}"
            }
        },
        {
            "name": "Python: Bot",
            "type": "python",
            "request": "launch",
            "module": "bot.main",
            "console": "integratedTerminal"
        },
        {
            "name": "Python: Tests",
            "type": "python",
            "request": "launch",
            "module": "pytest",
            "args": ["-v"],
            "console": "integratedTerminal"
        }
    ]
}
```

### Интерактивная отладка

```python
# Вставьте в код для точки останова
import pdb; pdb.set_trace()

# Или для breakpoint (Python 3.7+)
breakpoint()
```

**Команды pdb:**
- `n` (next) - следующая строка
- `s` (step) - войти в функцию
- `c` (continue) - продолжить выполнение
- `p variable` - вывести переменную
- `l` (list) - показать код вокруг
- `q` (quit) - выход

---

## Стиль кода

### Black (форматирование)

```bash
# Форматирование всех файлов
black .

# Проверка без изменений
black --check .

# Конкретный файл
black domain/services/sync_service.py
```

**pyproject.toml:**

```toml
[tool.black]
line-length = 100
target-version = ['py311']
exclude = '''
/(
    \.git
  | \.venv
  | venv
  | build
  | dist
)/
'''
```

### isort (сортировка импортов)

```bash
# Сортировка импортов
isort .

# Проверка
isort --check .
```

**pyproject.toml:**

```toml
[tool.isort]
profile = "black"
line_length = 100
```

### flake8 (линтинг)

```bash
# Проверка всего проекта
flake8

# Игнорирование ошибок
flake8 --ignore=E501,W503
```

**.flake8:**

```ini
[flake8]
max-line-length = 100
exclude = .git,__pycache__,venv,build,dist
ignore = E203,W503
```

### mypy (type checking)

```bash
# Проверка типов
mypy domain/ adapters/
```

**pyproject.toml:**

```toml
[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
ignore_missing_imports = true
```

### Pre-commit hooks

**.pre-commit-config.yaml:**

```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 23.7.0
    hooks:
      - id: black

  - repo: https://github.com/pycqa/isort
    rev: 5.12.0
    hooks:
      - id: isort

  - repo: https://github.com/pycqa/flake8
    rev: 6.1.0
    hooks:
      - id: flake8

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.5.1
    hooks:
      - id: mypy
        additional_dependencies: [types-requests]
```

**Установка:**

```bash
pip install pre-commit
pre-commit install
```

---

## Работа с Git

### Структура коммитов

**Формат:**

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Типы:**
- `feat` - новая функциональность
- `fix` - исправление бага
- `docs` - документация
- `style` - форматирование
- `refactor` - рефакторинг
- `test` - тесты
- `chore` - обслуживание

**Примеры:**

```bash
feat(vendors): add support for NEW_VENDOR

- Added NewVendorDownloader
- Updated registry configuration
- Added tests

Closes #42
```

```bash
fix(parser): handle empty price values

Previously parser crashed on empty price cells.
Now returns 0.0 for on-request items.

Fixes #55
```

### Ветвление

**GitFlow:**

```
main (production)
  └── develop (разработка)
        ├── feature/add-new-vendor
        ├── feature/export-command
        └── hotfix/parse-error
```

**Создание feature:**

```bash
git checkout develop
git checkout -b feature/add-new-vendor
# ... работа ...
git add .
git commit -m "feat(vendors): add NEW_VENDOR support"
git push origin feature/add-new-vendor
# Создать Pull Request
```

---

## Performance Optimization

### Профилирование

**cProfile:**

```bash
python -m cProfile -o profile.stats main.py KEAZ
```

**Анализ:**

```python
import pstats
p = pstats.Stats('profile.stats')
p.sort_stats('cumulative').print_stats(20)
```

**line_profiler:**

```bash
pip install line_profiler

# Добавьте @profile к функции
@profile
def sync_vendor(self, vendor: str):
    ...

# Запуск
kernprof -l -v main.py KEAZ
```

### Оптимизация БД

**Batch операции:**

```python
# ❌ Медленно
for item in items:
    repository.add_item(item)

# ✅ Быстро
repository.add_items(items)  # Один запрос
```

**Индексы:**

```sql
CREATE INDEX idx_vendor_article ON Total_Price(Vendor, Part_Num);
CREATE INDEX idx_status ON Total_Price(Status);
```

**EXPLAIN запросы:**

```python
query = "SELECT * FROM Total_Price WHERE Vendor = 'KEAZ'"
result = session.execute(f"EXPLAIN QUERY PLAN {query}")
print(list(result))
```

### Оптимизация памяти

**Generators вместо списков:**

```python
# ❌ Загружает все в память
def get_all_items():
    return [item for item in query_result]

# ✅ Итератор
def get_all_items():
    for item in query_result:
        yield item
```

**Chunked processing:**

```python
# pandas
for chunk in pd.read_excel(file, chunksize=1000):
    process_chunk(chunk)
```

---

## Troubleshooting для разработчиков

### Проблема: Тесты не находят модули

**Решение:**

```bash
# Установите проект в editable mode
pip install -e .

# Или добавьте PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:${PWD}"
```

### Проблема: Конфликт зависимостей

**Решение:**

```bash
# Пересоздайте venv
rm -rf venv
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Проблема: Unicode ошибки на Windows

**Решение:**

```python
# main.py
import sys
import codecs

if sys.platform == 'win32':
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer)
```

---

## Чек-лист перед релизом

- [ ] Все тесты проходят (`pytest`)
- [ ] Код отформатирован (`black`, `isort`)
- [ ] Линтинг пройден (`flake8`)
- [ ] Type checking пройден (`mypy`)
- [ ] Документация обновлена
- [ ] CHANGELOG.md обновлен
- [ ] Версия обновлена (semantic versioning)
- [ ] Проверено на тестовом окружении
- [ ] Создан Git tag (`git tag v1.2.0`)

---

**Составлено:** Claude Sonnet 4.5
**Дата:** 2026-01-07
