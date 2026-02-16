# Feature: Загрузка номенклатуры из 1C-ERP

## Описание

Добавить функцию загрузки номенклатуры из 1C-ERP через REST API эндпоинт. Данные из 1C содержат позиции от разных производителей, без цен. Загрузка в таблицу `Total_Price` с маппингом полей.

## Маппинг полей 1C → БД

| 1C (JSON)      | БД (Total_Price) | Примечание                    |
|----------------|------------------|-------------------------------|
| manufacturer   | Vendor           | Производитель                 |
| article        | Part_Num         | Артикул                       |
| name           | Descr            | Наименование                  |
| unit           | Units            | Единица измерения             |
| —              | Price            | Всегда 0                      |
| —              | PriceText        | Всегда "Цена по запросу"      |
| code           | ArticlePC        | Код 1C (например "УП-00540773") |

## Логика синхронизации

1. GET запрос к `ERP_BASE_URL` с Basic Auth (`ONE_C_LOGIN` / `ONE_C_PASSWORD`)
2. Парсим JSON массив позиций
3. Для каждой позиции:
   - **Шаг 1**: Ищем по `ArticlePC` (code) — если найдено, обновляем запись
   - **Шаг 2**: Если не найдено по ArticlePC, ищем по `Vendor + Part_Num` (manufacturer + article) — если найдено, обновляем (добавляем ArticlePC)
   - **Шаг 3**: Если не найдено нигде — INSERT новую запись
4. **Дубликаты ArticlePC**: если в ответе 1C есть 2+ позиции с одинаковым `code` — НЕ падаем, а логируем в отчет и пропускаем дубликаты

## Настройки (.env)

```
ONE_C_LOGIN=...
ONE_C_PASSWORD=...
ERP_BASE_URL=http://192.168.10.110:8080/erp_test8/hs/orders/Nomenklatura
```

## Задачи

### Задача 1: Добавить настройки 1C в `config/settings.py`

**Файл:** `config/settings.py`

Добавить:
```python
ONE_C_LOGIN = os.getenv('ONE_C_LOGIN', '')
ONE_C_PASSWORD = os.getenv('ONE_C_PASSWORD', '')
ERP_BASE_URL = os.getenv('ERP_BASE_URL', '')
```

### Задача 2: Создать ERP клиент `adapters/erp/erp_client.py`

**Новый файл:** `adapters/erp/erp_client.py`

Класс `ErpClient`:
- `__init__(base_url, login, password)` — инициализация HTTP-сессии с Basic Auth
- `fetch_nomenclature() -> List[dict]` — GET запрос к эндпоинту, возвращает список позиций
- Verbose логирование: количество полученных позиций, время запроса, ошибки HTTP

### Задача 3: Создать сервис загрузки `domain/services/erp_sync_service.py`

**Новый файл:** `domain/services/erp_sync_service.py`

Класс `ErpSyncService`:
- `__init__(erp_client, repository)`
- `sync_from_erp() -> ErpSyncResult`
  1. Загружает данные через `erp_client.fetch_nomenclature()`
  2. Проверяет дубликаты по `code` (ArticlePC) — дубликаты выносит в отчет
  3. Для каждой уникальной позиции:
     - Ищет по ArticlePC в БД
     - Если нет — ищет по Vendor+Part_Num
     - Если нашел — UPDATE
     - Если нет — INSERT
  4. Формирует результат: добавлено, обновлено, дубликатов, ошибок

### Задача 4: Расширить `SqlRepository` для работы с ArticlePC

**Файл:** `adapters/database/sql_repository.py`

Добавить методы:
- `find_by_article_pc(article_pc: str) -> Optional[dict]` — поиск по ArticlePC
- `find_by_vendor_part_num(vendor: str, part_num: str) -> Optional[dict]` — поиск по Vendor+Part_Num
- `upsert_erp_item(vendor, part_num, descr, units, article_pc) -> str` — INSERT или UPDATE с Price=0, PriceText="Цена по запросу"
- `bulk_upsert_erp_items(items: List[dict]) -> dict` — batch операция для производительности

### Задача 5: Создать dataclass `ErpSyncResult`

**Новый файл:** `domain/entities/erp_sync_result.py`

```python
@dataclass
class ErpSyncResult:
    total_received: int = 0
    added: int = 0
    updated: int = 0
    skipped_duplicates: int = 0
    errors: int = 0
    duplicate_codes: List[str] = field(default_factory=list)
    error_details: List[str] = field(default_factory=list)
```

### Задача 6: Добавить кнопку "Обновить из 1C-ERP" в Telegram бот

**Файл:** `bot/handlers.py`

- Добавить команду `/erp` или кнопку в `/start` / `/sync`
- Обработчик `erp_sync_command()` — запускает `ErpSyncService.sync_from_erp()`
- Callback `erp_sync_callback()` — обработка нажатия кнопки
- Формирование отчета: количество добавлено/обновлено, список дубликатов (если есть)

**Файл:** `bot/main.py`

- Зарегистрировать новый CommandHandler и CallbackQueryHandler

### Задача 7: Коммит и push

## Параметры реализации

- **Тесты:** нет
- **Логирование:** verbose (DEBUG)
- **Документация:** обновить после реализации
