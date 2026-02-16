import logging
import time
from typing import List

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


class ErpClient:
    """Клиент для работы с API 1C-ERP"""

    def __init__(self, base_url: str, login: str, password: str):
        self.base_url = base_url
        self.auth = HTTPBasicAuth(login, password)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })

    def fetch_nomenclature(self) -> List[dict]:
        """
        Загружает номенклатуру из 1C-ERP.

        Returns:
            Список позиций из 1C (каждая — dict с полями code, name, article, manufacturer, unit и т.д.)
        """
        logger.info(f"Запрос номенклатуры из 1C-ERP: {self.base_url}")
        start_time = time.time()

        try:
            response = self.session.get(self.base_url, timeout=120)
            response.raise_for_status()

            elapsed = time.time() - start_time
            data = response.json()

            if isinstance(data, list):
                items = data
            elif isinstance(data, dict) and 'items' in data:
                items = data['items']
            else:
                items = data if isinstance(data, list) else []

            logger.info(f"Получено {len(items)} позиций из 1C-ERP за {elapsed:.1f}с")
            logger.debug(f"Первая позиция (пример): {items[0] if items else 'пусто'}")

            return items

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP ошибка 1C-ERP: {e.response.status_code} - {e.response.text[:500]}")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Ошибка подключения к 1C-ERP ({self.base_url}): {e}")
            raise
        except requests.exceptions.Timeout:
            logger.error(f"Таймаут запроса к 1C-ERP (120с)")
            raise
        except Exception as e:
            logger.error(f"Неожиданная ошибка при запросе к 1C-ERP: {e}", exc_info=True)
            raise
