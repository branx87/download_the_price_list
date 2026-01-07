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
        # Добавляем таймаут для SQLite
        if 'sqlite' in database_url:
            database_url = database_url.replace('sqlite:///', 'sqlite:///') + '?timeout=30'
        self.engine = create_engine(database_url, connect_args={'timeout': 30} if 'sqlite' in database_url else {})
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.data_normalizer = DataNormalizer()
        self._ensure_indexes()

    def _ensure_indexes(self):
        """Создает индексы для ускорения работы с БД"""
        try:
            with self.SessionLocal() as session:
                session.execute(text("CREATE INDEX IF NOT EXISTS idx_vendor_article ON Total_Price(Vendor, Part_Num)"))
                session.execute(text("CREATE INDEX IF NOT EXISTS idx_vendor ON Total_Price(Vendor)"))
                session.commit()
                logger.info("✅ Индексы БД проверены/созданы")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось создать индексы: {e}")

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
                WHERE Vendor = :vendor AND Status != 'disappeared'
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

    def add_items(self, items: List[PriceItem]) -> int:
        """Добавить новые позиции"""
        if not items:
            return 0

        with self.SessionLocal() as session:
            query = text("""
                INSERT INTO Total_Price
                (Vendor, Part_Num, Descr, Price, Units, Storage, Status, updated_at)
                VALUES (:vendor, :article, :descr, :price, :units, :storage, 'new', :updated_at)
            """)

            current_time = datetime.now().isoformat()
            data = [
                {
                    "vendor": item.vendor,
                    "article": item.article,
                    "descr": item.description or "",
                    "price": float(item.price),
                    "units": item.units or "шт",
                    "storage": item.storage or "",
                    "updated_at": current_time
                }
                for item in items
            ]

            # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: используем connection.execute() для batch операции
            connection = session.connection()
            connection.execute(query, data)
            session.commit()
            return len(items)

    def update_items(self, items: List[PriceItem]) -> int:
        """Обновить существующие позиции (оптимизированная batch версия)"""
        if not items:
            return 0

        with self.SessionLocal() as session:
            current_time = datetime.now().isoformat()

            updated_count = 0

            # Batch update для всех вендоров
            if items:
                # Разбиваем на батчи по 1000 для оптимизации
                batch_size = 1000
                for i in range(0, len(items), batch_size):
                    batch = items[i:i + batch_size]
                    data = [
                        {
                            "price": float(item.price),
                            "descr": item.description or "",
                            "units": item.units or "шт",
                            "storage": item.storage or "",
                            "vendor": item.vendor,
                            "article": item.article,
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
                            Status = 'price_changed',
                            updated_at = :updated_at
                        WHERE Vendor = :vendor AND TRIM(Part_Num) = :article
                    """)

                    # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: используем connection.execute() с executemany
                    # для настоящей batch операции
                    connection = session.connection()
                    connection.execute(query, data)
                    updated_count += len(batch)

                    # Flush каждые 5000 записей для освобождения памяти
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
            current_time = datetime.now().isoformat()

            # SQLite имеет ограничение на количество параметров (~999)
            # Разбиваем articles на батчи по 500
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
                    AND TRIM(Part_Num) IN ({article_placeholders})
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

    def reset_changed_status(self, vendor: str) -> int:
        """Сбросить статус price_changed на active после синхронизации"""
        vendor_normalized = self.data_normalizer.normalize_vendor_name(vendor)

        with self.SessionLocal() as session:
            query = text("""
                UPDATE Total_Price
                SET Status = 'active'
                WHERE Vendor = :vendor AND Status = 'price_changed'
            """)

            result = session.execute(query, {'vendor': vendor_normalized})
            session.commit()
            return result.rowcount