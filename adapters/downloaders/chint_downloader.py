# adapters/downloaders/chint_downloader.py
from .base_downloader import BaseDownloader, MAX_RETRIES, RETRY_DELAYS
from pathlib import Path
from datetime import datetime, timedelta
import time
import logging
import requests

logger = logging.getLogger(__name__)


class ChintDownloader(BaseDownloader):
    # Шаблон актуального URL с датой — chint.ru начал публиковать
    # файлы с суффиксом даты вида Price-list-CHINT_DD-MM-YYYY.xlsx
    _DATE_URL_TEMPLATE = (
        "https://chint.ru/upload/price-list/date/Price-list-CHINT_{date}.xlsx"
    )
    # Статичные URL как запасные варианты
    _STATIC_URLS = [
        "https://chint.ru/upload/price-list/Price-list-CHINT.xlsx",
        "https://chint.ru/upload/price/CHINT-price.xlsx",
    ]
    # Сколько дней назад ищем дату-файл (CHINT обновляет нерегулярно)
    _DATE_LOOKBACK_DAYS = 14

    def _get_download_url(self, vendor: str) -> str:
        """Не используется — логика в download()"""
        return ""

    def _build_candidate_urls(self) -> list[str]:
        """Строит список URL для попытки загрузки: сначала датированные (от сегодня
        назад на _DATE_LOOKBACK_DAYS дней), затем статичные."""
        today = datetime.now()
        candidates = []
        for days_back in range(self._DATE_LOOKBACK_DAYS):
            dt = today - timedelta(days=days_back)
            date_str = dt.strftime("%d-%m-%Y")
            candidates.append(self._DATE_URL_TEMPLATE.format(date=date_str))
        candidates.extend(self._STATIC_URLS)
        return candidates

    def download(self, vendor: str) -> Path:
        """Пробуем датированные URL (от сегодня назад), затем статичные, с retry."""
        logger.info(f"📥 Загрузка файла для {vendor}")

        candidate_urls = self._build_candidate_urls()

        for attempt in range(1, MAX_RETRIES + 1):
            for url in candidate_urls:
                try:
                    logger.info(f"[FIX] CHINT: пробуем {url} (попытка {attempt}/{MAX_RETRIES})")
                    response = self.session.get(url, stream=True, timeout=60)

                    if response.status_code != 200:
                        logger.debug(f"[FIX] CHINT URL {url}: статус {response.status_code}, пропуск")
                        continue

                    filepath = self.storage.get_storage_path(vendor)
                    with open(filepath, 'wb') as f:
                        for chunk in response.iter_content(8192):
                            f.write(chunk)

                    file_size = filepath.stat().st_size
                    if file_size > 102400:
                        logger.info(f"[FIX] ✅ CHINT файл загружен: {url} → {filepath.name} ({file_size // 1024} KB)")
                        self.storage.cleanup_old_months(vendor, keep_months=3)
                        return filepath

                    logger.debug(f"[FIX] CHINT: файл маленький ({file_size} bytes) по {url}, пропуск")
                    filepath.unlink(missing_ok=True)

                except (requests.ConnectionError, requests.Timeout) as e:
                    logger.warning(f"[FIX] CHINT URL {url} недоступен: {e}")

            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[attempt - 1]
                logger.warning(f"[FIX] CHINT: все URL недоступны (попытка {attempt}). Повтор через {delay}с...")
                time.sleep(delay)

        raise Exception("CHINT: все URL не сработали после всех попыток")
