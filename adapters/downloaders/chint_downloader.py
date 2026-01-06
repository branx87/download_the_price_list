# adapters/downloaders/chint_downloader.py
from .base_downloader import BaseDownloader
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class ChintDownloader(BaseDownloader):
    def _get_download_url(self, vendor: str) -> str:
        """Не используется - логика в download()"""
        return ""
    
    def download(self, vendor: str) -> Path:
        """Переопределяем полностью - пробуем несколько URL"""
        logger.info(f"📥 Загрузка файла для {vendor}")
        
        direct_urls = [
            "https://chint.ru/upload/price-list/date/Price-list-CHINT.xlsx",
            "https://chint.ru/upload/price-list/Price-list-CHINT.xlsx", 
            "https://chint.ru/upload/price/CHINT-price.xlsx"
        ]
        
        for url in direct_urls:
            try:
                logger.info(f"CHINT: пробуем {url}")
                
                response = self.session.get(url, stream=True, timeout=60)
                
                if response.status_code == 200:
                    filepath = self.storage.get_storage_path(vendor)

                    with open(filepath, 'wb') as f:
                        for chunk in response.iter_content(1024):
                            f.write(chunk)

                    file_size = filepath.stat().st_size
                    if file_size > 102400:  # Больше 100KB
                        logger.info(f"✅ Файл загружен: {filepath.name} ({file_size//1024} KB)")
                        self.storage.cleanup_old_months(vendor, keep_months=3)
                        return filepath
                    else:
                        logger.warning(f"CHINT: файл маленький ({file_size} bytes)")
                        filepath.unlink()
                        
            except Exception as e:
                logger.warning(f"CHINT URL {url} не сработал: {e}")
                continue
        
        raise Exception("CHINT: все URL не сработали")