from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from decimal import Decimal
from domain.entities.price_item import PriceItem


@dataclass
class PriceChange:
    """Информация об изменении цены одной позиции"""
    article: str
    description: str
    old_price: Decimal
    new_price: Decimal

    @property
    def price_diff(self) -> Decimal:
        """Абсолютная разница в цене"""
        return self.new_price - self.old_price

    @property
    def price_diff_percent(self) -> float:
        """Процентное изменение цены"""
        if self.old_price == 0:
            return 0.0
        return float((self.new_price - self.old_price) / self.old_price * 100)


@dataclass
class PriceComparisonResult:
    """Результат сравнения прайс-листов без обновления БД"""

    vendor: str
    last_db_update: Optional[datetime] = None

    # Статистика
    total_in_file: int = 0
    total_in_db: int = 0

    new_items_count: int = 0
    updated_items_count: int = 0
    disappeared_items_count: int = 0

    # Детальная информация
    new_items: List[PriceItem] = field(default_factory=list)
    price_changes: List[PriceChange] = field(default_factory=list)
    disappeared_items: List[PriceItem] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Есть ли какие-то изменения"""
        return (self.new_items_count > 0 or
                self.updated_items_count > 0 or
                self.disappeared_items_count > 0)

    @property
    def max_price_increase(self) -> Optional[PriceChange]:
        """Максимальное повышение цены"""
        increases = [c for c in self.price_changes if c.price_diff > 0]
        return max(increases, key=lambda x: x.price_diff_percent) if increases else None

    @property
    def max_price_decrease(self) -> Optional[PriceChange]:
        """Максимальное понижение цены"""
        decreases = [c for c in self.price_changes if c.price_diff < 0]
        return min(decreases, key=lambda x: x.price_diff_percent) if decreases else None

    @property
    def avg_price_change_percent(self) -> float:
        """Среднее процентное изменение цен"""
        if not self.price_changes:
            return 0.0
        return sum(c.price_diff_percent for c in self.price_changes) / len(self.price_changes)
