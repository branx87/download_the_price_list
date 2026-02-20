from dataclasses import dataclass, field
from datetime import datetime
from typing import List
from domain.entities.price_item import PriceItem
from domain.entities.price_comparison import PriceChange


@dataclass
class SyncResult:
    """Результат синхронизации прайс-листа"""

    vendor: str
    success: bool
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime = field(default_factory=datetime.now)

    # Статистика
    total_items: int = 0
    new_items: int = 0
    updated_items: int = 0
    disappeared_items: int = 0

    # Детали
    error_message: str = ""
    file_path: str = ""

    # Списки для отчета
    added_items: List[PriceItem] = field(default_factory=list)
    updated_items_list: List[PriceItem] = field(default_factory=list)
    disappeared_items_list: List[PriceItem] = field(default_factory=list)
    price_changes_list: List[PriceChange] = field(default_factory=list)

    @property
    def execution_time(self) -> float:
        """Время выполнения в секундах"""
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def duration_seconds(self) -> float:
        """Длительность синхронизации в секундах"""
        return self.execution_time

    @property
    def changes_count(self) -> int:
        """Общее количество изменений"""
        return self.new_items + self.updated_items + self.disappeared_items

    @property
    def price_changes_count(self) -> int:
        """Количество позиций с изменением цены"""
        return len(self.price_changes_list)

    def __str__(self):
        status = "✅" if self.success else "❌"

        return (
            f"{status} {self.vendor}: "
            f"total={self.total_items}, "
            f"new={self.new_items}, "
            f"updated={self.updated_items}, "
            f"price_changes={self.price_changes_count}, "
            f"disappeared={self.disappeared_items}, "
            f"time={self.execution_time:.1f}s"
        )