import logging
from urllib.parse import quote, urlparse
from pathlib import Path

import requests

from adapters.downloaders.base_downloader import BaseDownloader

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class OwenDownloader(BaseDownloader):
    """Загрузчик для ОВЕН с обходом JS-challenge.

    Сервер owen.ru отдаёт HTML-страницу с JS-проверкой при первом запросе.
    JS вычисляет хеш (jhash) из параметра cookie __js_p_ и устанавливает
    cookies __jhash_ и __jua_, после чего повторный запрос отдаёт файл.
    """

    def __init__(self, download_dir, url: str):
        super().__init__(download_dir)
        self.url = url

    def _get_download_url(self, vendor: str) -> str:
        return self.url

    @staticmethod
    def _compute_jhash(code: int) -> int:
        """Вычисляет jhash — эмуляция JS-функции get_jhash."""
        x = 123456789
        k = 0
        for i in range(1677696):
            x = ((x + code) ^ (x + (x % 3) + (x % 17) + code) ^ i) % 16776960
            if x % 117 == 0:
                k = (k + 1) % 1111
        return k

    def _download_with_challenge(self, url: str) -> bytes:
        """Скачивает файл, обходя JS-challenge. Возвращает содержимое."""
        ua = self.session.headers.get('User-Agent', '')

        # 1. Первый запрос — получаем cookie __js_p_
        r1 = self.session.get(url, timeout=30)
        js_p = self.session.cookies.get('__js_p_')

        if not js_p:
            return r1.content

        parts = js_p.split(',')
        code = int(parts[0])
        jhash = self._compute_jhash(code)

        logger.info(f"[FIX] ОВЕН JS-challenge: code={code}, jhash={jhash}")

        # 2. Повторный запрос с вычисленными cookies
        cookie_str = f'__js_p_={js_p}; __jhash_={jhash}; __jua_={quote(ua)}'
        r2 = requests.get(url, headers={
            'User-Agent': ua,
            'Cookie': cookie_str,
        }, timeout=120)
        r2.raise_for_status()
        return r2.content

    def _download_file(self, url: str, vendor: str):
        """Загружает файл с обходом JS-challenge и проверкой результата."""
        content = None

        for attempt in range(1, MAX_RETRIES + 1):
            content = self._download_with_challenge(url)

            # Проверяем что скачался реальный файл, а не HTML
            if content[:4] == b'PK\x03\x04':
                logger.info(f"[FIX] ОВЕН: скачан Excel ({len(content)} байт), попытка {attempt}")
                break

            logger.warning(f"[FIX] ОВЕН: получен HTML вместо Excel, попытка {attempt}/{MAX_RETRIES}")
            # Сбрасываем cookies сессии перед повтором
            self.session.cookies.clear()
        else:
            raise ValueError(
                f"ОВЕН: не удалось скачать Excel после {MAX_RETRIES} попыток "
                f"(сервер отдаёт HTML-страницу JS-challenge)"
            )

        # Сохраняем файл
        extension = Path(urlparse(url).path).suffix or '.xlsx'
        file_path = self.storage.get_storage_path(vendor, extension=extension)

        with open(file_path, 'wb') as f:
            f.write(content)

        self.storage.cleanup_old_months(vendor, keep_months=3)
        return file_path
