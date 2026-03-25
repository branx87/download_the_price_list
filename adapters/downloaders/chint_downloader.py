# adapters/downloaders/chint_downloader.py
from .base_downloader import BaseDownloader, MAX_RETRIES, RETRY_DELAYS
from pathlib import Path
import time
import logging
import requests

logger = logging.getLogger(__name__)


class ChintDownloader(BaseDownloader):
    _URLS = [
        "https://chint.ru/upload/price-list/date/Price-list-CHINT.xlsx",
        "https://chint.ru/upload/price-list/Price-list-CHINT.xlsx",
        "https://chint.ru/upload/price/CHINT-price.xlsx",
    ]

    def _get_download_url(self, vendor: str) -> str:
        """Не используется - логика в download()"""
        return ""

    def download(self, vendor: str) -> Path:
        """Пробуем несколько URL с retry при сетевых ошибках."""
        logger.info(f"📥 Загрузка файла для {vendor}")

        for attempt in range(1, MAX_RETRIES + 1):
            for url in self._URLS:
                try:
                    logger.info(f"CHINT: пробуем {url} (попытка {attempt}/{MAX_RETRIES})")
                    response = self.session.get(url, stream=True, timeout=60)

                    if response.status_code != 200:
                        logger.warning(f"CHINT URL {url}: статус {response.status_code}")
                        continue

                    filepath = self.storage.get_storage_path(vendor)
                    with open(filepath, 'wb') as f:
                        for chunk in response.iter_content(1024):
                            f.write(chunk)

                    file_size = filepath.stat().st_size
                    if file_size > 102400:
                        logger.info(f"✅ Файл загружен: {filepath.name} ({file_size // 1024} KB)")
                        self.storage.cleanup_old_months(vendor, keep_months=3)
                        return filepath

                    logger.warning(f"CHINT: файл маленький ({file_size} bytes), пробуем следующий")
                    filepath.unlink(missing_ok=True)

                except (requests.ConnectionError, requests.Timeout) as e:
                    logger.warning(f"CHINT URL {url} не сработал: {e}")

            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt - 1]
                logger.warning(f"CHINT: все URL недоступны (попытка {attempt}). Повтор через {delay}с...")
                time.sleep(delay)

        raise Exception("CHINT: все URL не сработали после всех попыток")