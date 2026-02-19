import logging
import time
from typing import List, Set, Optional
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from domain.interfaces.repository import IRepository
from domain.entities.price_item import PriceItem
from domain.services.data_normalizer import DataNormalizer


logger = logging.getLogger(__name__)


class SqlRepository(IRepository):
    """Репозиторий для работы с SQL БД через SQLAlchemy"""

    def __init__(self, database_url: str):
        self.is_sqlite = 'sqlite' in database_url.lower()
        self.engine = create_engine(database_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.data_normalizer = DataNormalizer()
        self._synonyms_cache = None
        if self.is_sqlite:
            self._apply_sqlite_pragmas()
        self._ensure_indexes()
        self.cleanup_none_strings()
        self.cleanup_whitespace()

    def _apply_sqlite_pragmas(self):
        """Оптимизация SQLite для быстрой записи"""
        try:
            with self.SessionLocal() as session:
                session.execute(text("PRAGMA journal_mode = WAL"))
                session.execute(text("PRAGMA synchronous = NORMAL"))
                session.execute(text("PRAGMA cache_size = -64000"))  # 64MB
                session.execute(text("PRAGMA temp_store = MEMORY"))
                session.commit()
                logger.info("[FIX] SQLite PRAGMA оптимизации применены (WAL, cache 64MB)")
        except Exception as e:
            logger.warning(f"Не удалось применить SQLite PRAGMA: {e}")

    def _ensure_indexes(self):
        """Создает индексы для ускорения работы с БД (SQLite и MSSQL)"""
        indexes = [
            ('idx_vendor_article', 'Total_Price(Vendor, Part_Num)'),
            ('idx_vendor', 'Total_Price(Vendor)'),
            ('idx_article_pc', 'Total_Price(ArticlePC)'),
            ('idx_status', 'Total_Price(Status)'),
            ('idx_vendor_status', 'Total_Price(Vendor, Status)'),
        ]

        try:
            with self.SessionLocal() as session:
                created = []
                for idx_name, idx_columns in indexes:
                    try:
                        if self.is_sqlite:
                            session.execute(text(
                                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_columns}"
                            ))
                        else:
                            # MSSQL: проверяем через sys.indexes
                            exists = session.execute(text(
                                "SELECT 1 FROM sys.indexes WHERE name = :idx_name "
                                "AND object_id = OBJECT_ID('Total_Price')"
                            ), {'idx_name': idx_name}).fetchone()
                            if not exists:
                                session.execute(text(
                                    f"CREATE INDEX {idx_name} ON {idx_columns}"
                                ))
                                created.append(idx_name)
                    except Exception as e:
                        logger.debug(f"Индекс {idx_name}: {e}")

                session.commit()
                if created:
                    logger.info(f"[FIX] Созданы индексы: {', '.join(created)}")
                logger.info(f"Индексы БД проверены/созданы ({len(indexes)} шт)")
        except Exception as e:
            logger.warning(f"Не удалось создать индексы: {e}")

    def cleanup_none_strings(self):
        """Заменяет строки 'None' на пустые строки во всех текстовых полях.
        Каждая колонка — отдельная транзакция, чтобы не блокировать таблицу надолго."""
        # Discount, Labor исключены — числовые поля FLOAT, не могут содержать 'None'
        columns = ['Storage', 'Currency', 'URL', 'LaborCategory',
                   'ArticlePC', 'PriceText', 'Alt_Part_Num']
        total_fixed = 0
        for col in columns:
            try:
                with self.SessionLocal() as session:
                    result = session.execute(
                        text(f"UPDATE Total_Price SET {col} = '' WHERE {col} = 'None'")
                    )
                    session.commit()
                    if result.rowcount > 0:
                        logger.info(f"[FIX] Очищено {result.rowcount} значений 'None' в колонке {col}")
                        total_fixed += result.rowcount
            except Exception as e:
                logger.error(f"Ошибка очистки 'None' в колонке {col}: {e}")
        if total_fixed > 0:
            logger.info(f"[FIX] Итого очищено {total_fixed} значений 'None' в БД")
        else:
            logger.debug("[FIX] Строк 'None' в БД не обнаружено")

    def cleanup_whitespace(self):
        """Обрезает пробелы в ключевых полях Vendor, Part_Num, ArticlePC.
        Актуально для MSSQL — данные из 1C часто содержат пробелы в начале/конце."""
        if self.is_sqlite:
            trim = lambda col: f"TRIM({col})"
            not_trimmed = lambda col: f"{col} != TRIM({col})"
        else:
            trim = lambda col: f"LTRIM(RTRIM({col}))"
            not_trimmed = lambda col: f"{col} != LTRIM(RTRIM({col}))"

        fields = ['Vendor', 'Part_Num', 'ArticlePC']
        total_fixed = 0
        for col in fields:
            try:
                with self.SessionLocal() as session:
                    result = session.execute(text(
                        f"UPDATE Total_Price SET {col} = {trim(col)} "
                        f"WHERE {col} IS NOT NULL AND {col} != '' "
                        f"AND {not_trimmed(col)}"
                    ))
                    session.commit()
                    if result.rowcount > 0:
                        logger.info(f"[FIX] Обрезаны пробелы в {col}: {result.rowcount} записей")
                        total_fixed += result.rowcount
            except Exception as e:
                logger.error(f"Ошибка обрезки пробелов в {col}: {e}")
        # Дополнительно: убираем ВНУТРЕННИЕ пробелы из Part_Num.
        # normalize_article() делает re.sub(r'\s+', '', article), но cleanup_whitespace
        # ранее убирал только LEADING/TRAILING. Из-за этого несоответствия
        # bulk_set_article_pc не мог найти запись в БД по нормализованному Part_Num
        # и ArticlePC никогда не сохранялся → одни и те же позиции "привязывались" снова и снова.
        try:
            with self.SessionLocal() as session:
                result = session.execute(text(
                    "UPDATE Total_Price "
                    "SET Part_Num = REPLACE(Part_Num, ' ', '') "
                    "WHERE Part_Num IS NOT NULL AND Part_Num != '' "
                    "AND Part_Num != REPLACE(Part_Num, ' ', '')"
                ))
                session.commit()
                if result.rowcount > 0:
                    logger.info(f"[FIX] Убраны внутренние пробелы из Part_Num: {result.rowcount} записей")
                    total_fixed += result.rowcount
        except Exception as e:
            logger.error(f"[FIX] Ошибка нормализации внутренних пробелов Part_Num: {e}")

        if total_fixed > 0:
            logger.info(f"[FIX] Итого обрезано пробелов: {total_fixed} записей")
        else:
            logger.debug("[FIX] Лишних пробелов в ключевых полях не обнаружено")

    @property
    def _nolock(self) -> str:
        """Возвращает WITH (NOLOCK) для MSSQL, пустую строку для SQLite"""
        return '' if self.is_sqlite else 'WITH (NOLOCK)'

    @staticmethod
    def _safe_str(value, default: str = "") -> str:
        """Преобразует значение в строку, заменяя None и 'None' на default"""
        if value is None:
            return default
        s = str(value).strip()
        if s in ('None', 'none', 'NONE', 'null', 'NULL'):
            return default
        return s

    def fix_null_statuses(self) -> int:
        """Проставляет Status='active' для записей с NULL Status (после db_copy)"""
        try:
            with self.SessionLocal() as session:
                result = session.execute(
                    text("UPDATE Total_Price SET Status = 'active' WHERE Status IS NULL")
                )
                session.commit()
                count = result.rowcount
                if count > 0:
                    logger.info(f"[FIX] Проставлен Status='active' для {count} записей с NULL Status")
                return count
        except Exception as e:
            logger.error(f"[FIX] Ошибка fix_null_statuses: {e}")
            return 0

    def get_current_articles(self, vendor: str) -> Set[str]:
        """Получить все текущие артикулы вендора"""
        with self.SessionLocal() as session:
            query = text(f"""
                SELECT Part_Num
                FROM Total_Price {self._nolock}
                WHERE Vendor = :vendor
            """)
            result = session.execute(query, {"vendor": vendor})
            return {row[0] for row in result}

    def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
        """Получить все позиции вендора"""
        # Нормализуем vendor name для поиска
        vendor_normalized = self.data_normalizer.normalize_vendor_name(vendor)

        with self.SessionLocal() as session:
            query = text(f"""
                SELECT Vendor, Part_Num, Descr, Price, Units, Storage
                FROM Total_Price {self._nolock}
                WHERE Vendor = :vendor AND (Status IS NULL OR Status != 'disappeared')
            """)

            result = session.execute(query, {'vendor': vendor_normalized})

            items = []
            for row in result:
                try:
                    # Нормализуем данные при чтении из БД
                    article_normalized = self.data_normalizer.normalize_article(row[1], vendor_normalized)
                    unit_normalized = self.data_normalizer.normalize_unit(row[4] if row[4] else 'шт')

                    item = PriceItem(
                        vendor=vendor_normalized,  # Всегда возвращаем нормализованное имя
                        article=article_normalized,
                        description=row[2] or "",
                        price=Decimal(str(row[3])) if row[3] else Decimal('0'),
                        units=unit_normalized,
                        storage=row[5] or ""
                    )
                    items.append(item)
                except Exception as e:
                    logger.warning(f"Пропущена запись: {e}")

            return items

    def add_items(self, items: List[PriceItem], synonyms_map: dict = None) -> int:
        """Добавить новые позиции (батчами по 500 с промежуточным commit)"""
        if not items:
            return 0

        synonyms_map = synonyms_map or {}

        query = text("""
            INSERT INTO Total_Price
            (Vendor, Part_Num, Descr, Price, Units, Storage, VendorForFilter, Status, updated_at)
            VALUES (:vendor, :article, :descr, :price, :units, :storage, :vendor_for_filter, 'new', :updated_at)
        """)

        current_time = datetime.now()
        all_data = [
            {
                "vendor": self._safe_str(item.vendor),
                "article": self._safe_str(item.article),
                "descr": self._safe_str(item.description),
                "price": float(item.price),
                "units": self._safe_str(item.units, "шт"),
                "storage": self._safe_str(item.storage),
                "vendor_for_filter": self.resolve_vendor_for_filter(
                    self._safe_str(item.vendor), synonyms_map
                ),
                "updated_at": current_time
            }
            for item in items
        ]

        batch_size = 500
        total = 0
        for i in range(0, len(all_data), batch_size):
            batch = all_data[i:i + batch_size]
            with self.SessionLocal() as session:
                connection = session.connection()
                connection.execute(query, batch)
                session.commit()
                total += len(batch)

        return total

    def update_items(self, items: List[PriceItem], synonyms_map: dict = None) -> int:
        """
        Обновить существующие позиции.

        MSSQL: временная таблица + один JOIN UPDATE (вместо N построчных UPDATE).
        SQLite: executemany батчами по 500.
        """
        if not items:
            return 0

        synonyms_map = synonyms_map or {}
        current_time = datetime.now()
        all_data = [
            {
                "price": float(item.price),
                "descr": self._safe_str(item.description),
                "units": self._safe_str(item.units, "шт"),
                "storage": self._safe_str(item.storage),
                "vendor": self._safe_str(item.vendor),
                "article": self._safe_str(item.article),
                "vendor_for_filter": self.resolve_vendor_for_filter(
                    self._safe_str(item.vendor), synonyms_map
                ),
                "updated_at": current_time
            }
            for item in items
        ]

        if not self.is_sqlite:
            # MSSQL: временная таблица + один JOIN UPDATE
            start_time = time.time()
            with self.SessionLocal() as session:
                try:
                    connection = session.connection()
                    connection.execute(text("""
                        CREATE TABLE #tmp_price_upd (
                            Vendor NVARCHAR(255),
                            Part_Num NVARCHAR(255),
                            Price FLOAT,
                            Descr NVARCHAR(MAX),
                            Units NVARCHAR(255),
                            Storage NVARCHAR(255),
                            VendorForFilter NVARCHAR(255),
                            updated_at DATETIME
                        )
                    """))
                    batch_size = 1000
                    for i in range(0, len(all_data), batch_size):
                        connection.execute(text("""
                            INSERT INTO #tmp_price_upd
                                (Vendor, Part_Num, Price, Descr, Units, Storage, VendorForFilter, updated_at)
                            VALUES
                                (:vendor, :article, :price, :descr, :units, :storage, :vendor_for_filter, :updated_at)
                        """), all_data[i:i + batch_size])
                    result = connection.execute(text("""
                        UPDATE tp
                        SET tp.Price = t.Price,
                            tp.Descr = t.Descr,
                            tp.Units = t.Units,
                            tp.Storage = t.Storage,
                            tp.VendorForFilter = t.VendorForFilter,
                            tp.Status = 'price_changed',
                            tp.updated_at = t.updated_at
                        FROM Total_Price tp
                        INNER JOIN #tmp_price_upd t
                            ON tp.Vendor = t.Vendor AND tp.Part_Num = t.Part_Num
                    """))
                    affected = result.rowcount
                    connection.execute(text("DROP TABLE #tmp_price_upd"))
                    session.commit()
                except Exception as e:
                    logger.error(f"[FIX] Ошибка update_items: {e}", exc_info=True)
                    session.rollback()
                    raise
            elapsed = time.time() - start_time
            logger.info(
                f"[FIX] update_items (temp table): {affected}/{len(items)} обновлено "
                f"за {elapsed:.1f}с ({affected / elapsed:.0f} rec/s)"
            )
            return affected
        else:
            # SQLite: executemany батчами
            query = text("""
                UPDATE Total_Price
                SET Price = :price,
                    Descr = :descr,
                    Units = :units,
                    Storage = :storage,
                    VendorForFilter = :vendor_for_filter,
                    Status = 'price_changed',
                    updated_at = :updated_at
                WHERE Vendor = :vendor AND Part_Num = :article
            """)
            updated_count = 0
            batch_size = 500
            for i in range(0, len(all_data), batch_size):
                with self.SessionLocal() as session:
                    connection = session.connection()
                    connection.execute(query, all_data[i:i + batch_size])
                    session.commit()
                    updated_count += len(all_data[i:i + batch_size])
            return updated_count

    def mark_as_disappeared(self, vendor: str, articles: List[str]) -> int:
        """Пометить позиции как исчезнувшие (батчами по 500 с промежуточным commit)"""
        if not articles:
            return 0

        # Нормализуем vendor name
        vendor_normalized = self.data_normalizer.normalize_vendor_name(vendor)
        current_time = datetime.now()

        batch_size = 500
        updated_count = 0

        for i in range(0, len(articles), batch_size):
            batch = articles[i:i + batch_size]

            # Формируем плейсхолдеры для IN clause (артикулы)
            article_placeholders = ', '.join([f':article{j}' for j in range(len(batch))])

            query = text(f"""
                UPDATE Total_Price
                SET Status = 'disappeared',
                    updated_at = :updated_at
                WHERE Vendor = :vendor
                AND Part_Num IN ({article_placeholders})
            """)

            # Формируем параметры
            params = {'vendor': vendor_normalized}
            params.update({f'article{j}': art for j, art in enumerate(batch)})
            params['updated_at'] = current_time

            with self.SessionLocal() as session:
                result = session.execute(query, params)
                session.commit()
                updated_count += result.rowcount

        # Debug: логируем если обновлено меньше чем ожидалось
        if updated_count < len(articles):
            logger.warning(f"⚠️ Обновлено {updated_count} из {len(articles)} исчезнувших позиций")

        return updated_count

    def delete_old_disappeared(self, vendor: str, days: int = 30):
        # Нормализуем vendor name
        vendor_normalized = self.data_normalizer.normalize_vendor_name(vendor)

        cutoff_date = datetime.now() - timedelta(days=days)

        query = text("""
            DELETE FROM Total_Price
            WHERE Vendor = :vendor
            AND Status = 'disappeared'
            AND updated_at < :cutoff_date
        """)

        with self.SessionLocal() as session:
            session.execute(query, {'vendor': vendor_normalized, 'cutoff_date': cutoff_date})
            session.commit()

    def get_vendor_last_update(self, vendor: str) -> Optional[datetime]:
        """Получить дату последнего обновления вендора"""
        vendor_normalized = self.data_normalizer.normalize_vendor_name(vendor)

        with self.SessionLocal() as session:
            query = text(f"""
                SELECT MAX(updated_at)
                FROM Total_Price {self._nolock}
                WHERE Vendor = :vendor
            """)

            result = session.execute(query, {'vendor': vendor_normalized}).fetchone()

            if result and result[0]:
                # Преобразуем строку в datetime, если нужно
                if isinstance(result[0], str):
                    return datetime.fromisoformat(result[0])
                return result[0]
            return None

    def get_vendor_total_count(self, vendor: str) -> int:
        """Получить общее количество позиций вендора (включая исчезнувшие)"""
        vendor_normalized = self.data_normalizer.normalize_vendor_name(vendor)

        with self.SessionLocal() as session:
            query = text(f"""
                SELECT COUNT(*)
                FROM Total_Price {self._nolock}
                WHERE Vendor = :vendor
            """)

            result = session.execute(query, {'vendor': vendor_normalized}).fetchone()
            return result[0] if result else 0

    # ========== Методы для 1C-ERP ==========

    def find_by_article_pc(self, article_pc: str) -> bool:
        """Проверяет, существует ли запись с данным ArticlePC"""
        with self.SessionLocal() as session:
            query = text(f"""
                SELECT 1 FROM Total_Price {self._nolock}
                WHERE ArticlePC = :article_pc
            """)
            result = session.execute(query, {'article_pc': article_pc}).fetchone()
            return result is not None

    def find_by_vendor_part_num(self, vendor: str, part_num: str) -> bool:
        """Проверяет, существует ли запись по Vendor + Part_Num"""
        with self.SessionLocal() as session:
            query = text(f"""
                SELECT 1 FROM Total_Price {self._nolock}
                WHERE Vendor = :vendor AND Part_Num = :part_num
            """)
            result = session.execute(query, {'vendor': vendor, 'part_num': part_num}).fetchone()
            return result is not None

    def set_article_pc(self, vendor: str, part_num: str, article_pc: str) -> int:
        """Устанавливает ArticlePC для существующей записи по Vendor+Part_Num"""
        with self.SessionLocal() as session:
            query = text("""
                UPDATE Total_Price
                SET ArticlePC = :article_pc,
                    updated_at = :updated_at
                WHERE Vendor = :vendor AND Part_Num = :part_num
            """)
            result = session.execute(query, {
                'article_pc': article_pc,
                'vendor': vendor,
                'part_num': part_num,
                'updated_at': datetime.now()
            })
            session.commit()
            return result.rowcount

    def add_erp_items(self, items: list) -> int:
        """
        Безопасный batch INSERT позиций из 1C-ERP.

        Если запись уже существует (Vendor+Part_Num) — обновляет ТОЛЬКО ArticlePC,
        не трогая Price, LaborCategory и другие поля.
        Операции разбиты на мелкие транзакции для снижения блокировок MSSQL.

        Каждый элемент items — dict с ключами:
        vendor, part_num, descr, units, article_pc, vendor_for_filter
        """
        if not items:
            return 0

        current_time = datetime.now()

        # Шаг 1: Подготовка данных
        items_prepared = [
            {
                'vendor': self._safe_str(item['vendor']),
                'part_num': self._safe_str(item['part_num']),
                'descr': self._safe_str(item['descr']),
                'units': self._safe_str(item['units'], 'шт'),
                'article_pc': self._safe_str(item['article_pc']),
                'vendor_for_filter': self._safe_str(item.get('vendor_for_filter', '1C-ERP')),
            }
            for item in items
        ]

        # Шаг 2: Pre-check — какие записи уже есть в БД (отдельная транзакция, NOLOCK)
        vendors_in_batch = set(i['vendor'] for i in items_prepared)
        existing_in_db = set()

        with self.SessionLocal() as session:
            for vendor in vendors_in_batch:
                parts_for_vendor = [i['part_num'] for i in items_prepared if i['vendor'] == vendor]
                for j in range(0, len(parts_for_vendor), 500):
                    batch_parts = parts_for_vendor[j:j + 500]
                    placeholders = ', '.join([f':p{k}' for k in range(len(batch_parts))])
                    params = {'vendor': vendor}
                    params.update({f'p{k}': p for k, p in enumerate(batch_parts)})

                    result = session.execute(text(f"""
                        SELECT Part_Num FROM Total_Price {self._nolock}
                        WHERE Vendor = :vendor AND Part_Num IN ({placeholders})
                    """), params)
                    for row in result:
                        existing_in_db.add((vendor, row[0]))

        # Шаг 3: Разделяем — новые vs уже существующие
        to_insert = []
        to_update_pc = []
        for item in items_prepared:
            key = (item['vendor'], item['part_num'])
            if key in existing_in_db:
                to_update_pc.append(item)
            else:
                to_insert.append(item)

        # Шаг 4: UPDATE ArticlePC для существующих (не трогаем Price/LaborCategory)
        # MSSQL: временная таблица + один JOIN UPDATE; SQLite: executemany батчами
        if to_update_pc:
            start_time = time.time()
            update_data = [
                {
                    'article_pc': item['article_pc'],
                    'vendor': item['vendor'],
                    'part_num': item['part_num'],
                    'updated_at': current_time
                }
                for item in to_update_pc
            ]

            if not self.is_sqlite:
                # MSSQL: временная таблица + один JOIN UPDATE
                with self.SessionLocal() as session:
                    try:
                        connection = session.connection()
                        connection.execute(text("""
                            CREATE TABLE #tmp_erp_upd (
                                Vendor NVARCHAR(255),
                                Part_Num NVARCHAR(255),
                                ArticlePC NVARCHAR(255),
                                updated_at DATETIME
                            )
                        """))
                        batch_size = 1000
                        for i in range(0, len(update_data), batch_size):
                            connection.execute(text("""
                                INSERT INTO #tmp_erp_upd (Vendor, Part_Num, ArticlePC, updated_at)
                                VALUES (:vendor, :part_num, :article_pc, :updated_at)
                            """), update_data[i:i + batch_size])
                        result = connection.execute(text("""
                            UPDATE tp
                            SET tp.ArticlePC = t.ArticlePC,
                                tp.updated_at = t.updated_at
                            FROM Total_Price tp
                            INNER JOIN #tmp_erp_upd t
                                ON tp.Vendor = t.Vendor AND tp.Part_Num = t.Part_Num
                            WHERE tp.ArticlePC IS NULL OR tp.ArticlePC = ''
                        """))
                        updated_total = result.rowcount
                        connection.execute(text("DROP TABLE #tmp_erp_upd"))
                        session.commit()
                    except Exception as e:
                        logger.error(f"[FIX] Ошибка в add_erp_items UPDATE: {e}", exc_info=True)
                        session.rollback()
                        raise
            else:
                # SQLite: executemany батчами
                update_query = text("""
                    UPDATE Total_Price
                    SET ArticlePC = :article_pc,
                        updated_at = :updated_at
                    WHERE Vendor = :vendor AND Part_Num = :part_num
                    AND (ArticlePC IS NULL OR ArticlePC = '')
                """)
                updated_total = 0
                with self.SessionLocal() as session:
                    try:
                        for i in range(0, len(update_data), 1000):
                            connection = session.connection()
                            connection.execute(update_query, update_data[i:i + 1000])
                            session.commit()
                            updated_total += len(update_data[i:i + 1000])
                    except Exception as e:
                        logger.error(f"[FIX] Ошибка в add_erp_items UPDATE: {e}", exc_info=True)
                        session.rollback()
                        raise

            elapsed_total = time.time() - start_time
            method = 'temp table' if not self.is_sqlite else 'executemany'
            logger.info(
                f"[FIX] ERP ({method}): {updated_total}/{len(to_update_pc)} ArticlePC обновлено "
                f"за {elapsed_total:.1f}с ({len(to_update_pc) / elapsed_total:.0f} rec/s)"
            )

        # Шаг 5: INSERT только реально новых записей (батчами по 1000)
        inserted = 0
        if to_insert:
            start_time = time.time()
            insert_query = text("""
                INSERT INTO Total_Price
                (Vendor, Part_Num, Descr, Price, Units, PriceText, ArticlePC, VendorForFilter, Status, updated_at)
                VALUES (:vendor, :part_num, :descr, 0, :units, :price_text, :article_pc, :vendor_for_filter, 'active', :updated_at)
            """)
            batch_size = 1000
            num_batches = (len(to_insert) + batch_size - 1) // batch_size

            # Используем ОДНУ сессию для всех INSERT батчей
            with self.SessionLocal() as session:
                try:
                    for batch_idx in range(0, len(to_insert), batch_size):
                        batch = to_insert[batch_idx:batch_idx + batch_size]
                        batch_num = batch_idx // batch_size + 1
                        insert_data = [
                            {
                                'vendor': item['vendor'],
                                'part_num': item['part_num'],
                                'descr': item['descr'],
                                'units': item['units'],
                                'price_text': 'Цена по запросу',
                                'article_pc': item['article_pc'],
                                'vendor_for_filter': item['vendor_for_filter'],
                                'updated_at': current_time
                            }
                            for item in batch
                        ]
                        # Получаем connection на каждую итерацию — после commit() он возвращается в пул
                        connection = session.connection()
                        connection.execute(insert_query, insert_data)
                        session.commit()
                        inserted += len(batch)

                        # Пауза между батчами чтобы не блокировать БД
                        if batch_num < num_batches:
                            time.sleep(0.05)

                        # Прогресс-лог каждые 10000 записей или для последнего батча
                        if inserted % 10000 < batch_size or batch_num == num_batches:
                            elapsed = time.time() - start_time
                            logger.info(
                                f"[FIX] add_erp_items INSERT: {inserted}/{len(to_insert)} обработано, "
                                f"батч {batch_num}/{num_batches}, время: {elapsed:.1f}с"
                            )

                except Exception as e:
                    logger.error(f"[FIX] Ошибка в add_erp_items INSERT на батче {batch_num}/{num_batches}: {e}", exc_info=True)
                    session.rollback()
                    raise

            elapsed_total = time.time() - start_time
            logger.info(
                f"[FIX] ERP: {inserted} новых записей добавлено за {elapsed_total:.1f}с "
                f"({inserted / elapsed_total:.0f} rec/s)"
            )

        return inserted

    def bulk_set_article_pc(self, items: list) -> int:
        """
        Batch UPDATE ArticlePC для записей найденных по Vendor+Part_Num.

        MSSQL: временная таблица + один JOIN UPDATE (вместо N построчных UPDATE).
        SQLite: executemany батчами по 1000.

        Каждый элемент items — dict с ключами: vendor, part_num, article_pc
        """
        if not items:
            return 0

        start_time = time.time()
        current_time = datetime.now()
        all_data = [
            {
                'article_pc': self._safe_str(item['article_pc']),
                'vendor': self._safe_str(item['vendor']),
                'part_num': self._safe_str(item['part_num']),
                'updated_at': current_time
            }
            for item in items
        ]

        if not self.is_sqlite:
            # MSSQL: временная таблица + один JOIN UPDATE
            with self.SessionLocal() as session:
                try:
                    connection = session.connection()
                    connection.execute(text("""
                        CREATE TABLE #tmp_apc (
                            Vendor NVARCHAR(255),
                            Part_Num NVARCHAR(255),
                            ArticlePC NVARCHAR(255),
                            updated_at DATETIME
                        )
                    """))
                    batch_size = 1000
                    for i in range(0, len(all_data), batch_size):
                        connection.execute(text("""
                            INSERT INTO #tmp_apc (Vendor, Part_Num, ArticlePC, updated_at)
                            VALUES (:vendor, :part_num, :article_pc, :updated_at)
                        """), all_data[i:i + batch_size])
                    result = connection.execute(text("""
                        UPDATE tp
                        SET tp.ArticlePC = t.ArticlePC,
                            tp.updated_at = t.updated_at
                        FROM Total_Price tp
                        INNER JOIN #tmp_apc t
                            ON tp.Vendor = t.Vendor AND tp.Part_Num = t.Part_Num
                    """))
                    affected = result.rowcount
                    connection.execute(text("DROP TABLE #tmp_apc"))
                    session.commit()
                except Exception as e:
                    logger.error(f"[FIX] Ошибка bulk_set_article_pc: {e}", exc_info=True)
                    session.rollback()
                    raise
        else:
            # SQLite: executemany батчами
            query = text("""
                UPDATE Total_Price
                SET ArticlePC = :article_pc,
                    updated_at = :updated_at
                WHERE Vendor = :vendor AND Part_Num = :part_num
            """)
            affected = 0
            batch_size = 1000
            with self.SessionLocal() as session:
                try:
                    for i in range(0, len(all_data), batch_size):
                        connection = session.connection()
                        connection.execute(query, all_data[i:i + batch_size])
                        session.commit()
                        affected += len(all_data[i:i + batch_size])
                except Exception as e:
                    logger.error(f"[FIX] Ошибка bulk_set_article_pc: {e}", exc_info=True)
                    session.rollback()
                    raise

        elapsed = time.time() - start_time
        method = 'temp table' if not self.is_sqlite else 'executemany'
        logger.info(
            f"[FIX] bulk_set_article_pc ({method}): {affected}/{len(all_data)} обновлено "
            f"за {elapsed:.1f}с ({affected / elapsed:.0f} rec/s)"
        )
        if affected == 0 and len(all_data) > 0:
            logger.warning(
                f"[FIX] bulk_set_article_pc: 0 строк обновлено при {len(all_data)} входных записях — "
                f"несоответствие Vendor/Part_Num между кодом и БД"
            )
        return affected

    def get_all_article_pcs(self) -> set:
        """
        Получить все существующие ArticlePC из БД с нормализацией.

        Применяем .strip() и .upper() для консистентности с ERP sync.
        """
        with self.SessionLocal() as session:
            query = text(f"""
                SELECT ArticlePC FROM Total_Price {self._nolock}
                WHERE ArticlePC IS NOT NULL AND ArticlePC != ''
            """)
            result = session.execute(query)
            return {row[0].strip().upper() for row in result if row[0]}

    def get_all_vendor_part_num_pairs(self) -> set:
        """
        Получить все существующие пары Vendor+Part_Num с ПОЛНОЙ нормализацией.

        КРИТИЧНО: Применяем ту же нормализацию что и в ERP sync (DataNormalizer),
        иначе возникают дубли из-за несовпадения регистра/пробелов.
        """
        with self.SessionLocal() as session:
            query = text(f"""
                SELECT Vendor, Part_Num FROM Total_Price {self._nolock}
                WHERE Part_Num IS NOT NULL AND Part_Num != ''
            """)
            result = session.execute(query)
            # Применяем нормализацию ТОЧНО как в erp_sync_service.py
            return {
                (
                    DataNormalizer.normalize_vendor_name(row[0]) if row[0] else '',
                    DataNormalizer.normalize_article(row[1]) if row[1] else ''
                )
                for row in result
            }

    def reset_changed_status(self, vendor: str) -> int:
        """Сбросить статус price_changed/new/NULL на active после синхронизации"""
        vendor_normalized = self.data_normalizer.normalize_vendor_name(vendor)

        with self.SessionLocal() as session:
            query = text("""
                UPDATE Total_Price
                SET Status = 'active'
                WHERE Vendor = :vendor
                AND (Status = 'price_changed' OR Status = 'new' OR Status IS NULL OR Status = 'updated')
                AND Status != 'disappeared'
            """)

            result = session.execute(query, {'vendor': vendor_normalized})
            session.commit()
            return result.rowcount

    # ========== VendorSynonyms ==========

    def get_all_synonyms(self) -> list:
        """Получить все синонимы из VendorSynonyms"""
        with self.SessionLocal() as session:
            query = text(
                "SELECT ID, Vendor, VendorForFilter, CreatedAt, UpdatedAt "
                "FROM VendorSynonyms ORDER BY VendorForFilter, Vendor"
            )
            result = session.execute(query)
            return [
                {'id': row[0], 'vendor': row[1], 'vendor_for_filter': row[2],
                 'created_at': row[3], 'updated_at': row[4]}
                for row in result
            ]

    def get_synonyms_map(self) -> dict:
        """Получить словарь Vendor -> VendorForFilter"""
        with self.SessionLocal() as session:
            query = text("SELECT Vendor, VendorForFilter FROM VendorSynonyms")
            result = session.execute(query)
            return {row[0]: row[1] for row in result}

    def get_synonyms_cached(self) -> dict:
        """Получить кэшированный маппинг синонимов"""
        if self._synonyms_cache is None:
            self._synonyms_cache = self.get_synonyms_map()
        return self._synonyms_cache

    def invalidate_synonyms_cache(self):
        """Сбросить кэш синонимов"""
        self._synonyms_cache = None

    def add_synonym(self, vendor: str, vendor_for_filter: str) -> int:
        """Добавить синоним вендора"""
        with self.SessionLocal() as session:
            query = text("""
                INSERT INTO VendorSynonyms (Vendor, VendorForFilter, CreatedAt, UpdatedAt)
                VALUES (:vendor, :vendor_for_filter, :now, :now)
            """)
            result = session.execute(query, {
                'vendor': vendor,
                'vendor_for_filter': vendor_for_filter,
                'now': datetime.now()
            })
            session.commit()
            self.invalidate_synonyms_cache()
            return result.rowcount

    def delete_synonym(self, synonym_id: int) -> int:
        """Удалить синоним по ID"""
        with self.SessionLocal() as session:
            query = text("DELETE FROM VendorSynonyms WHERE ID = :id")
            result = session.execute(query, {'id': synonym_id})
            session.commit()
            self.invalidate_synonyms_cache()
            return result.rowcount

    @staticmethod
    def resolve_vendor_for_filter(vendor: str, synonyms_map: dict, default: str = None) -> str:
        """Определить VendorForFilter по имени вендора.

        Если default=None — возвращает сам vendor (для sync прайсов).
        Если default задан (напр. '1C-ERP') — возвращает default (для ERP).
        """
        if vendor in synonyms_map:
            return synonyms_map[vendor]
        return default if default is not None else vendor

    def backfill_vendor_for_filter(self, synonyms_map: dict) -> int:
        """Заполнить VendorForFilter для записей где он NULL или пустой"""
        total_updated = 0
        with self.SessionLocal() as session:
            # Сначала применяем синонимы
            for vendor, vff in synonyms_map.items():
                result = session.execute(text("""
                    UPDATE Total_Price
                    SET VendorForFilter = :vff
                    WHERE Vendor = :vendor
                    AND (VendorForFilter IS NULL OR VendorForFilter = '')
                """), {'vff': vff, 'vendor': vendor})
                total_updated += result.rowcount

            # Затем для остальных: VendorForFilter = Vendor
            result = session.execute(text("""
                UPDATE Total_Price
                SET VendorForFilter = Vendor
                WHERE VendorForFilter IS NULL OR VendorForFilter = ''
            """))
            total_updated += result.rowcount
            session.commit()
        return total_updated