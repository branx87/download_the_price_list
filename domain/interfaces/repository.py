from abc import ABC, abstractmethod
from typing import List, Set, Optional
from datetime import datetime
from domain.entities.price_item import PriceItem



class IRepository(ABC):
    """Интерфейс для работы с хранилищем данных"""

    @abstractmethod
    def get_current_articles(self, vendor: str) -> Set[str]:
        """Получить текущие артикулы вендора из БД"""
        pass

    @abstractmethod
    def get_items_by_vendor(self, vendor: str) -> List[PriceItem]:
        """Получить все позиции вендора"""
        pass

    @abstractmethod
    def add_items(self, items: List[PriceItem]) -> int:
        """Добавить новые позиции"""
        pass

    @abstractmethod
    def update_items(self, items: List[PriceItem]) -> int:
        """Обновить существующие позиции"""
        pass

    @abstractmethod
    def mark_as_disappeared(self, vendor: str, articles: List[str]) -> int:
        """Пометить позиции как исчезнувшие"""
        pass

    @abstractmethod
    def delete_old_disappeared(self, vendor: str, days: int = 30) -> int:
        """Удалить старые исчезнувшие позиции"""
        pass

    def get_disappeared_articles(self, vendor: str) -> Set[str]:
        """Артикулы со статусом disappeared. Дефолт: пустое множество."""
        return set()

    def get_disappeared_items(self, vendor: str) -> List[PriceItem]:
        """Позиции со статусом disappeared. Дефолт: пустой список."""
        return []

    def restore_disappeared(self, vendor: str, articles: List[str]) -> int:
        """Восстановить позиции из disappeared в active. Дефолт: 0."""
        return 0

    def mark_price_on_request(self, vendor: str, articles: List[str]) -> int:
        """Пометить позиции как 'Цена по запросу'. Дефолт: 0."""
        return 0

    @abstractmethod
    def get_vendor_last_update(self, vendor: str) -> Optional[datetime]:
        """Получить дату последнего обновления вендора"""
        pass

    @abstractmethod
    def get_vendor_total_count(self, vendor: str) -> int:
        """Получить общее количество позиций вендора (включая исчезнувшие)"""
        pass

    @abstractmethod
    def reset_changed_status(self, vendor: str) -> int:
        """Сбросить статус price_changed на active после синхронизации"""
        pass

    def get_discontinued_articles(self, vendor: str) -> Set[str]:
        """Артикулы со статусом discontinued. Дефолт: пустое множество."""
        return set()
