from abc import ABC, abstractmethod
from pathlib import Path
from typing import List
from domain.entities.price_item import PriceItem


class IParser(ABC):
    """Интерфейс для парсинга прайс-листов"""

    @abstractmethod
    def parse(self, file_path: Path, vendor: str) -> List[PriceItem]:
        """
        Парсит файл прайс-листа.

        Args:
            file_path: Путь к файлу
            vendor: Название вендора

        Returns:
            List[PriceItem]: Список товарных позиций

        Raises:
            ParseError: Если парсинг не удался
        """
        pass
