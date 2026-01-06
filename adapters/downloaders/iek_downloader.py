# adapters/downloaders/iek_downloader.py
from .base_downloader import BaseDownloader
from pathlib import Path
from datetime import datetime
import zipfile
import time
import logging

logger = logging.getLogger(__name__)

class IekDownloader(BaseDownloader):
    def _get_download_url(self, vendor: str) -> str:
        """Не используется - логика в download()"""
        return ""
    
    def download(self, vendor: str) -> Path:
        """Переопределяем полностью - специальная логика для IEK"""
        logger.info(f"📥 Загрузка файла для {vendor}")

        final_filepath = self.storage.get_storage_path(vendor)
        temp_filepath = final_filepath.parent / (final_filepath.stem + "_temp.zip")

        url = "https://www.iek.ru/products/price/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        data = {'submit': 'скачать прайс-лист'}

        try:
            logger.info(f"POST загрузка IEK")
            response = self.session.post(url, headers=headers, data=data, stream=True, timeout=60)
            response.raise_for_status()

            # Сохраняем временный ZIP
            with open(temp_filepath, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)

            response.close()

            # Распаковываем ZIP
            try:
                with zipfile.ZipFile(temp_filepath, 'r') as zip_ref:
                    excel_files = [f for f in zip_ref.namelist()
                                   if f.lower().endswith(('.xls', '.xlsx'))]

                    if not excel_files:
                        raise ValueError("В архиве IEK не найдено Excel файлов")

                    excel_data = zip_ref.read(excel_files[0])

                    with open(final_filepath, 'wb') as f:
                        f.write(excel_data)

                    logger.info(f"✅ Файл загружен: {final_filepath.name}")

                time.sleep(0.5)
                temp_filepath.unlink(missing_ok=True)
                self.storage.cleanup_old_months(vendor, keep_months=3)
                return final_filepath

            except zipfile.BadZipFile:
                logger.warning("IEK: не ZIP, пробуем как Excel")
                if temp_filepath.stat().st_size > 1024:
                    time.sleep(0.5)
                    temp_filepath.rename(final_filepath)
                    logger.info(f"✅ Файл загружен: {final_filepath.name}")
                    self.storage.cleanup_old_months(vendor, keep_months=3)
                    return final_filepath
                else:
                    raise ValueError("IEK: пустой файл")

        except Exception as e:
            logger.error(f"❌ Ошибка загрузки IEK: {e}")
            temp_filepath.unlink(missing_ok=True)
            if final_filepath.exists():
                final_filepath.unlink(missing_ok=True)
            raise