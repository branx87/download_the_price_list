from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

from adapters.downloaders.simple_http import SimpleHttpDownloader
from adapters.downloaders.auth_http import AuthHttpDownloader
from adapters.downloaders.iek_downloader import IekDownloader
from adapters.downloaders.chint_downloader import ChintDownloader
from adapters.downloaders.ekf_downloader import EkfDownloader
from adapters.downloaders.owen_downloader import OwenDownloader
from adapters.downloaders.upload_downloader import UploadDownloader
from adapters.parsers.excel_parser import ExcelParser
from adapters.parsers.akel_parser import AkelParser
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
    # Если задан — create_parser() вернёт экземпляр этого класса вместо ExcelParser
    parser_class: Optional[type] = None


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

            'ОВЕН': VendorConfig(
                name='ОВЕН',
                downloader_class=OwenDownloader,
                downloader_params={
                    'download_dir': self.download_dir,
                    'url': 'https://owen.ru/downloads/price_owen.xlsx'
                },
                parser_config={
                    'engine': 'openpyxl',
                    'concat_article_to_description': True,
                    'columns': {
                        'article': 'Артикул',
                        'description': 'Наименование полное',
                        'price': 'Цена с НДС',
                        'units': 'шт',
                        'storage': 'Срок поставки'
                    }
                }
            ),

            'EKF': VendorConfig(
                name='EKF',
                downloader_class=EkfDownloader,
                downloader_params={
                    'download_dir': self.download_dir
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
                    # Строки 5 и 6 (0-based) объединяются в заголовок, данные с строки 7
                    'header_rows_combine': [5, 6],
                    'data_start_row': 7,
                    'columns': {
                        'article': 'Артикул',
                        'description': 'Наименование',
                        'price': 'Базовая цена с НДС (22%)',
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

            # ========== МЕКО (загрузка файла вручную через бота) ==========
            'МЕКО': VendorConfig(
                name='МЕКО',
                downloader_class=UploadDownloader,
                downloader_params={},
                parser_config={
                    'engine': 'openpyxl',
                    'sheet_name_pattern': 'Прайс',
                    'header_row': 0,
                    'columns': {
                        'article': 'Артикул',
                        'description': 'Наименование',
                        'price': 'Цена с НДС, руб.',
                    }
                }
            ),

            # ========== SE / Систем Электрик (загрузка файла вручную через бота) ==========
            'SE': VendorConfig(
                name='SE',
                downloader_class=UploadDownloader,
                downloader_params={},
                parser_config={
                    'engine': 'openpyxl',
                    'sheet_name_pattern': 'Тариф Москва',
                    'header_row': 2,
                    'columns': {
                        'article': 'Референс',
                        'description': 'Описание референса',
                        # поиск по подстроке — дата в названии меняется каждый прайс
                        'price': 'без НДС',
                        'units': 'Единица измерения',
                        'storage': 'со склада\nМосква',
                    }
                }
            ),

            # ========== AKEL (загрузка файла вручную через бота) ==========
            'AKEL': VendorConfig(
                name='AKEL',
                # UploadDownloader не используется через registry — хендлер создаёт его напрямую.
                # Указываем класс только для полноты записи в реестре.
                downloader_class=UploadDownloader,
                downloader_params={},
                parser_config={},
                parser_class=AkelParser,
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

        if config.parser_class is not None:
            return config.parser_class()

        return ExcelParser(config.parser_config, self.normalizer)
