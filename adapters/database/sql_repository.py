import logging
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
        self.engine = create_engine(database_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.data_normalizer = DataNormalizer()
        self._synonyms_cache = None
        self._ensure_indexes()
        self.cleanup_none_strings()

    def _ensure_indexes(self):
        """Создает индексы для ускорения работы с БД"""
        try:
            with self.SessionLocal() as session:
                for idx_name, idx_sql in [
                    ('idx_vendor_article', 'CREATE INDEX idx_vendor_article ON Total_Price(Vendor, Part_Num)'),
                    ('idx_vendor', 'CREATE INDEX idx_vendor ON Total_Price(Vendor)'),
                ]:
                    exists = session.execute(text(
                        "SELECT 1 FROM sys.indexes WHERE name = :idx_name "
                        "AND object_id = OBJECT_ID('Total_Price')"
                    ), {'idx_name': idx_name}).fetchone()
                    if not exists:
                        session.execute(text(idx_sql))
                session.commit()
                logger.info("Индексы БД проверены/созданы")
        except Exception as e:
            logger.warning(f"Не удалось создать индексы: {e}")

    def cleanup_none_strings(self):
        """Заменяет строки 'None' на пустые строки во всех текстовых полях"""
        columns = ['Storage', 'Currency', 'URL', 'Labor', 'LaborCategory',
                   'ArticlePC', 'Discount', 'PriceText', 'Alt_Part_Num']
        try:
            with self.SessionLocal() as session:
                total_fixed = 0
                for col in columns:
                    result = session.execute(
                        text(f"UPDATE Total_Price SET {col} = '' WHERE {col} = 'None'")
                    )
                    if result.rowcount > 0:
                        logger.info(f"[FIX] Очищено {result.rowcount} значений 'None' в колонке {col}")
                        total_fixed += result.rowcount
                session.commit()
                if total_fixed > 0:
                    logger.info(f"[FIX] Итого очищено {total_fixed} значений 'None' в БД")
                else:
                    logger.debug("[FIX] Строк 'None' в БД не обнаружено")
        except Exception as e:
            logger.error(f"Ошибка очистки 'None' строк: {e}")

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
            query = text("""
                SELECT Part_Num 
                FROM Total_Price 
                WHERE Vendor = :vendor
            """)
            result = session.execute(query, {"vendor": vendor})
            return {row[0] for row in result}

    def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
        """Получить все позиции вендора"""
        # Нормализуем vendor name для поиска
        vendor_normalized = self.data_normalizer.normalize_vendor_name(vendor)

        with self.SessionLocal() as session:
            query = text("""
                SELECT Vendor, Part_Num, Descr, Price, Units, Storage
                FROM Total_Price
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
        """Добавить новые позиции"""
        if not items:
            return 0

        synonyms_map = synonyms_map or {}

        with self.SessionLocal() as session:
            query = text("""
                INSERT INTO Total_Price
                (Vendor, Part_Num, Descr, Price, Units, Storage, VendorForFilter, Status, updated_at)
                VALUES (:vendor, :article, :descr, :price, :units, :storage, :vendor_for_filter, 'new', :updated_at)
            """)

            current_time = datetime.now()
            data = [
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

            connection = session.connection()
            connection.execute(query, data)
            session.commit()
            return len(items)

    def update_items(self, items: List[PriceItem], synonyms_map: dict = None) -> int:
        """Обновить существующие позиции (оптимизированная batch версия)"""
        if not items:
            return 0

        synonyms_map = synonyms_map or {}

        with self.SessionLocal() as session:
            current_time = datetime.now()

            updated_count = 0

            if items:
                batch_size = 1000
                for i in range(0, len(items), batch_size):
                    batch = items[i:i + batch_size]
                    data = [
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
                        for item in batch
                    ]

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

                    connection = session.connection()
                    connection.execute(query, data)
                    updated_count += len(batch)

                    if i % 5000 == 0 and i > 0:
                        session.flush()

            session.commit()
            return updated_count

    def mark_as_disappeared(self, vendor: str, articles: List[str]) -> int:
        """Пометить позиции как исчезнувшие (оптимизированная batch версия)"""
        if not articles:
            return 0

        # Нормализуем vendor name
        vendor_normalized = self.data_normalizer.normalize_vendor_name(vendor)

        with self.SessionLocal() as session:
            current_time = datetime.now()

            batch_size = 2000
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

                result = session.execute(query, params)
                updated_count += result.rowcount

            session.commit()

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
            query = text("""
                SELECT MAX(updated_at)
                FROM Total_Price
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
            query = text("""
                SELECT COUNT(*)
                FROM Total_Price
                WHERE Vendor = :vendor
            """)

            result = session.execute(query, {'vendor': vendor_normalized}).fetchone()
            return result[0] if result else 0

    # ========== Методы для 1C-ERP ==========

    def find_by_article_pc(self, article_pc: str) -> bool:
        """Проверяет, существует ли запись с данным ArticlePC"""
        with self.SessionLocal() as session:
            query = text("""
                SELECT 1 FROM Total_Price
                WHERE ArticlePC = :article_pc
            """)
            result = session.execute(query, {'article_pc': article_pc}).fetchone()
            return result is not None

    def find_by_vendor_part_num(self, vendor: str, part_num: str) -> bool:
        """Проверяет, существует ли запись по Vendor + Part_Num"""
        with self.SessionLocal() as session:
            query = text("""
                SELECT 1 FROM Total_Price
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
        Batch INSERT позиций из 1C-ERP.

        Каждый элемент items — dict с ключами:
        vendor, part_num, descr, units, article_pc, vendor_for_filter
        """
        if not items:
            return 0

        with self.SessionLocal() as session:
            query = text("""
                INSERT INTO Total_Price
                (Vendor, Part_Num, Descr, Price, Units, PriceText, ArticlePC, VendorForFilter, Status, updated_at)
                VALUES (:vendor, :part_num, :descr, 0, :units, :price_text, :article_pc, :vendor_for_filter, 'active', :updated_at)
            """)

            current_time = datetime.now()
            data = [
                {
                    'vendor': self._safe_str(item['vendor']),
                    'part_num': self._safe_str(item['part_num']),
                    'descr': self._safe_str(item['descr']),
                    'units': self._safe_str(item['units'], 'шт'),
                    'price_text': 'Цена по запросу',
                    'article_pc': self._safe_str(item['article_pc']),
                    'vendor_for_filter': self._safe_str(item.get('vendor_for_filter', '1C-ERP')),
                    'updated_at': current_time
                }
                for item in items
            ]

            connection = session.connection()
            connection.execute(query, data)
            session.commit()
            return len(items)

    def bulk_set_article_pc(self, items: list) -> int:
        """
        Batch UPDATE ArticlePC для записей найденных по Vendor+Part_Num.

        Каждый элемент items — dict с ключами: vendor, part_num, article_pc
        """
        if not items:
            return 0

        with self.SessionLocal() as session:
            query = text("""
                UPDATE Total_Price
                SET ArticlePC = :article_pc,
                    updated_at = :updated_at
                WHERE Vendor = :vendor AND Part_Num = :part_num
            """)

            current_time = datetime.now()
            data = [
                {
                    'article_pc': self._safe_str(item['article_pc']),
                    'vendor': self._safe_str(item['vendor']),
                    'part_num': self._safe_str(item['part_num']),
                    'updated_at': current_time
                }
                for item in items
            ]

            connection = session.connection()
            connection.execute(query, data)
            session.commit()
            return len(items)

    def get_all_article_pcs(self) -> set:
        """Получить все существующие ArticlePC из БД"""
        with self.SessionLocal() as session:
            query = text("""
                SELECT ArticlePC FROM Total_Price
                WHERE ArticlePC IS NOT NULL AND ArticlePC != ''
            """)
            result = session.execute(query)
            return {row[0] for row in result}

    def get_all_vendor_part_num_pairs(self) -> set:
        """Получить все существующие пары Vendor+Part_Num"""
        with self.SessionLocal() as session:
            query = text("""
                SELECT Vendor, Part_Num FROM Total_Price
            """)
            result = session.execute(query)
            return {(row[0], row[1]) for row in result}

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