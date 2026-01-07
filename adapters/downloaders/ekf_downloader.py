import logging
from pathlib import Path
from datetime import datetime, timedelta
import requests

from adapters.downloaders.base_downloader import BaseDownloader


logger = logging.getLogger(__name__)


class EkfDownloader(BaseDownloader):
    """
    Загрузчик для EKF с fallback на несколько дат.

    EKF публикует файлы с датой в имени, но не каждый день.
    Пробуем несколько дат начиная с сегодня и до 7 дней назад.
    """

    def _get_download_url(self, vendor: str) -> str:
        """Получить URL с fallback на несколько дат"""
        base_url = "https://api.ekfgroup.com/storage/prices/ekf_pricelist_{}.xlsx"

        # Пробуем даты: сегодня, вчера, ... до 7 дней назад
        for days_back in range(8):
            target_date = datetime.now() - timedelta(days=days_back)
            date_str = target_date.strftime("%Y-%m-%d")
            url = base_url.format(date_str)

            # Проверяем доступность URL (HEAD запрос)
            try:
                response = self.session.head(url, timeout=10)
                if response.status_code == 200:
                    logger.info(f"✅ EKF: найден файл за {date_str}")
                    return url
                else:
                    logger.debug(f"EKF: файл за {date_str} недоступен (статус {response.status_code})")
            except requests.RequestException as e:
                logger.debug(f"EKF: ошибка при проверке {date_str}: {e}")
                continue

        # Если ничего не нашли, возвращаем URL с сегодняшней датой
        # (чтобы получить понятную ошибку 404)
        fallback_url = base_url.format(datetime.now().strftime("%Y-%m-%d"))
        logger.warning(f"⚠️ EKF: файлы за последние 7 дней не найдены, используем {fallback_url}")
        return fallback_url
