from pathlib import Path
from typing import Dict, Any
from dataclasses import dataclass

from adapters.downloaders.simple_http import SimpleHttpDownloader
from adapters.downloaders.auth_http import AuthHttpDownloader
from adapters.downloaders.iek_downloader import IekDownloader
from adapters.downloaders.chint_downloader import ChintDownloader
from adapters.parsers.excel_parser import ExcelParser
from utils.normalizer import ArticleNormalizer
from adapters.downloaders.dkc_downloader import DkcDownloader

from datetime import datetime

@dataclass
class VendorConfig:
    """Конфигурация одного вендора"""
    name: str
    downloader_class: type
    downloader_params: Dict[str, Any]
    parser_config: Dict[str, Any]


class VendorRegistry:
    """Реестр всех вендоров"""

    def __init__(self, download_dir: Path, normalizer: ArticleNormalizer):
        self.download_dir = download_dir
        self.normalizer = normalizer
        self._vendors = self._init_vendors()

    def _init_vendors(self) -> Dict[str, VendorConfig]:
        """Инициализация конфигураций всех вендоров"""
        return {
            'KEAZ': VendorConfig(
                name='KEAZ',
                downloader_class=SimpleHttpDownloader,
                downloader_params={
                    'download_dir': self.download_dir,
                    'url': 'https://files.keaz.ru/ftp/keaz.xls'
                },
                parser_config={
                    'engine': 'xlrd',
                    'columns': {
                        'article': 'Артикул',
                        'description': 'Номенклатура',
                        'price': 'Цена (с НДС) руб.',
                        'units': 'Ед.',
                        'storage': 'Складской статус в Курске'
                    }
                }
            ),

            'OWEN': VendorConfig(
                name='OWEN',
                downloader_class=SimpleHttpDownloader,
                downloader_params={
                    'download_dir': self.download_dir,
                    'url': 'https://owen.ru/downloads/price_owen.xlsx'
                },
                parser_config={
                    'engine': 'openpyxl',
                    'columns': {
                        'article': 'Артикул',
                        'description': 'Наименование рабочее',
                        'price': 'Цена с НДС',
                        'units': 'шт',
                        'storage': 'Срок поставки'
                    }
                }
            ),

            'EKF': VendorConfig(
                name='EKF',
                downloader_class=SimpleHttpDownloader,
                downloader_params={
                    'download_dir': self.download_dir,
                    'url': f'https://api.ekfgroup.com/storage/prices/ekf_pricelist_{datetime.now().strftime("%Y-%m-%d")}.xlsx'
                },
                parser_config={
                    'engine': 'openpyxl',
                    'columns': {
                        'article': 'Артикул',
                        'description': 'Номенклатура',
                        'price': 'Базовая цена,                   с НДС',
                        'units': 'Ед.'
                    }
                }
            ),

            # ========== ИСПРАВЛЕННЫЙ IEK ==========
            'IEK': VendorConfig(
                name='IEK',
                downloader_class=IekDownloader,
                downloader_params={'download_dir': self.download_dir},
                parser_config={
                    'engine': 'openpyxl',
                    'columns': {
                        'article': 'Артикул',
                        'description': 'Наименование',
                        'price': 'Базовая цена с НДС',  # ← После объединения
                        'units': 'Ед.'
                    }
                }
            ),

            'DKC': VendorConfig(
                name='DKC',
                downloader_class=DkcDownloader,
                downloader_params={'download_dir': self.download_dir},
                parser_config={
                    'engine': 'openpyxl',
                    'columns': {
                        'article': 'Код',
                        'description': 'Описание',
                        'price': 'Цена с НДС, руб./м(шт)',  # или 'Price' - проверь в файле
                        'units': 'Ед. Изм.'
                    }
                }
            ),

            # ========== ИСПРАВЛЕННЫЙ CHINT ==========
            'CHINT': VendorConfig(
                name='CHINT',
                downloader_class=ChintDownloader,  # ← Специальный downloader
                downloader_params={
                    'download_dir': self.download_dir
                    # URL не нужен - они внутри ChintDownloader
                },
                parser_config={
                    'engine': 'openpyxl',
                    'columns': {
                        'article': 'Артикул',
                        'description': 'Наименование',
                        'price': 'Тариф с НДС, руб',
                        'units': 'Ед.'
                    }
                }
            ),
        }

    def get_vendor_names(self) -> list:
        """Получить список всех вендоров"""
        return list(self._vendors.keys())

    def create_downloader(self, vendor: str):
        """Создать загрузчик для вендора"""
        config = self._vendors.get(vendor)
        if not config:
            raise ValueError(f"Неизвестный вендор: {vendor}")

        return config.downloader_class(**config.downloader_params)

    def create_parser(self, vendor: str):
        """Создать парсер для вендора"""
        config = self._vendors.get(vendor)
        if not config:
            raise ValueError(f"Неизвестный вендор: {vendor}")

        return ExcelParser(config.parser_config, self.normalizer)
