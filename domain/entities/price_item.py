from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class PriceItem:
    """Товарная позиция в прайс-листе"""

    vendor: str
    article: str
    description: str
    price: Decimal
    units: str = "шт"
    storage: str = ""
    # Код 1С (ArticlePC в БД). Заполняется парсерами, которые видят
    # колонку «Код» / «Артикул РС» / «Код 1С» и т.п. Опциональное поле —
    # старые парсеры его не заполняют, и тогда код 1С не обновляется.
    code_1c: str = ""

    def __post_init__(self):
        """Валидация после инициализации"""
        if not self.vendor:
            raise ValueError("Vendor cannot be empty")
        if not self.article:
            raise ValueError("Article cannot be empty")
        if self.price < 0:
            raise ValueError("Price cannot be negative")

    @property
    def unique_key(self) -> str:
        """Уникальный ключ для идентификации позиции"""
        return f"{self.vendor}_{self.article}"

    def has_price_changed(self, other: 'PriceItem', threshold: float = 0.01) -> bool:
        """Проверка значимого изменения цены"""
        if not isinstance(other, PriceItem):
            return False
        return abs(float(self.price) - float(other.price)) > threshold
