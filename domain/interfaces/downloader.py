from abc import ABC, abstractmethod
from pathlib import Path


class IDownloader(ABC):
    """Интерфейс для загрузки прайс-листов"""

    @abstractmethod
    def download(self, vendor: str) -> Path:
        """
        Загружает файл прайс-листа.

        Args:
            vendor: Название вендора

        Returns:
            Path: Путь к загруженному файлу

        Raises:
            DownloadError: Если загрузка не удалась
        """
        pass
