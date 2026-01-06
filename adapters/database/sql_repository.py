import logging
from typing import List, Set
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from domain.interfaces.repository import IRepository
from domain.entities.price_item import PriceItem


logger = logging.getLogger(__name__)


class SqlRepository(IRepository):
    """Репозиторий для работы с SQL БД через SQLAlchemy"""

    def __init__(self, database_url: str):
        # Добавляем таймаут для SQLite
        if 'sqlite' in database_url:
            database_url = database_url.replace('sqlite:///', 'sqlite:///') + '?timeout=30'
        self.engine = create_engine(database_url, connect_args={'timeout': 30} if 'sqlite' in database_url else {})
        self.SessionLocal = sessionmaker(bind=self.engine)

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
        with self.SessionLocal() as session:
            query = text("""
                SELECT Vendor, Part_Num, Descr, Price, Units, Storage
                FROM Total_Price
                WHERE Vendor = :vendor AND Status != 'disappeared'
            """)
            result = session.execute(query, {"vendor": vendor})

            items = []
            for row in result:
                try:
                    item = PriceItem(
                        vendor=row[0],
                        article=row[1],
                        description=row[2] or "",
                        price=Decimal(str(row[3])) if row[3] else Decimal('0'),
                        units=row[4] or "шт",
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
                    "descr": item.description,
                    "price": float(item.price),
                    "units": item.units,
                    "storage": item.storage,
                    "updated_at": current_time
                }
                for item in items
            ]

            result = session.execute(query, data)
            session.commit()
            return result.rowcount

    def update_items(self, items: List[PriceItem]) -> int:
        """Обновить существующие позиции"""
        if not items:
            return 0

        with self.SessionLocal() as session:
            query = text("""
                UPDATE Total_Price
                SET Price = :price,
                    Descr = :descr,
                    Units = :units,
                    Storage = :storage,
                    Status = 'price_changed',
                    updated_at = :updated_at
                WHERE Vendor = :vendor AND Part_Num = :article
            """)

            current_time = datetime.now().isoformat()
            data = [
                {
                    "vendor": item.vendor,
                    "article": item.article,
                    "descr": item.description,
                    "price": float(item.price),
                    "units": item.units,
                    "storage": item.storage,
                    "updated_at": current_time
                }
                for item in items
            ]

            result = session.execute(query, data)
            session.commit()
            return result.rowcount

    def mark_as_disappeared(self, vendor: str, articles: List[str]) -> int:
        """Пометить позиции как исчезнувшие"""
        if not articles:
            return 0

        with self.SessionLocal() as session:
            query = text("""
                UPDATE Total_Price
                SET Status = 'disappeared',
                    updated_at = :updated_at
                WHERE Vendor = :vendor AND Part_Num = :article
            """)

            current_time = datetime.now().isoformat()
            data = [
                {
                    "vendor": vendor,
                    "article": article,
                    "updated_at": current_time
                }
                for article in articles
            ]

            result = session.execute(query, data)
            session.commit()
            return result.rowcount

    def delete_old_disappeared(self, vendor: str, days: int = 30):
        cutoff_date = datetime.now() - timedelta(days=days)
        query = text("""
            DELETE FROM Total_Price
            WHERE Vendor = :vendor 
            AND Status = 'disappeared'
            AND Last_Updated < :cutoff_date
        """)