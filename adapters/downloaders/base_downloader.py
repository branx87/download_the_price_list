import logging
import requests
from pathlib import Path
from datetime import datetime
from abc import ABC, abstractmethod

from domain.interfaces.downloader import IDownloader
from utils.file_storage import PriceFileStorage


logger = logging.getLogger(__name__)


class BaseDownloader(IDownloader, ABC):
    """Базовый класс для всех загрузчиков"""

    def __init__(self, download_dir: Path):
        self.download_dir = download_dir
        self.storage = PriceFileStorage(download_dir)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/138.0.0.0 Safari/537.36'
        })

    def download(self, vendor: str) -> Path:
        """Основной метод загрузки"""
        logger.info(f"📥 Загрузка файла для {vendor}")

        try:
            url = self._get_download_url(vendor)
            file_path = self._download_file(url, vendor)
            self._validate_file(file_path)
            logger.info(f"✅ Файл загружен: {file_path.name}")
            return file_path
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки {vendor}: {e}")
            raise

    @abstractmethod
    def _get_download_url(self, vendor: str) -> str:
        """Получить URL для загрузки (реализуется в подклассах)"""
        pass

    def _download_file(self, url: str, vendor: str) -> Path:
        """Загружает файл по URL"""
        file_path = self.storage.get_storage_path(vendor)

        response = self.session.get(url, stream=True, timeout=60)
        response.raise_for_status()

        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        # Очищаем старые месяцы (храним последние 3 месяца)
        self.storage.cleanup_old_months(vendor, keep_months=3)

        return file_path

    def _validate_file(self, file_path: Path):
        """Проверяет валидность загруженного файла"""
        if not file_path.exists():
            raise FileNotFoundError(f"Файл не найден: {file_path}")

        file_size = file_path.stat().st_size
        if file_size < 10240:  # Меньше 10KB
            raise ValueError(f"Файл слишком маленький ({file_size} bytes)")
