"""
Управление хранением прайс-файлов.

Правила хранения:
- Файлы организованы по месяцам
- Максимум 3 файла на месяц (начало/середина/конец)
- Старые файлы автоматически удаляются
"""
from pathlib import Path
from datetime import datetime
from typing import List
import logging

logger = logging.getLogger(__name__)


class PriceFileStorage:
    """Управляет хранением прайс-файлов с автоматической очисткой."""

    # Периоды месяца для хранения файлов
    PERIOD_START = (1, 14)   # 1-14 число
    PERIOD_MID = (15, 15)     # 15 число
    PERIOD_END = (16, 31)     # 16-31 число

    MAX_FILES_PER_PERIOD = 1  # Один файл на период

    def __init__(self, base_dir: Path):
        """
        Args:
            base_dir: Базовая директория для хранения файлов
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True, parents=True)

    def get_storage_path(self, vendor: str, timestamp: datetime = None) -> Path:
        """
        Возвращает путь для сохранения файла с автоматической очисткой старых файлов.

        Args:
            vendor: Название вендора
            timestamp: Временная метка (по умолчанию - текущее время)

        Returns:
            Path: Путь для сохранения файла
        """
        if timestamp is None:
            timestamp = datetime.now()

        # Создаем структуру: price_files/VENDOR/YYYY-MM/
        vendor_dir = self.base_dir / vendor / timestamp.strftime('%Y-%m')
        vendor_dir.mkdir(exist_ok=True, parents=True)

        # Очищаем старые файлы перед сохранением нового
        self._cleanup_old_files(vendor_dir, timestamp)

        # Формируем имя файла
        filename = f"{vendor}_price_{timestamp.strftime('%Y%m%d_%H%M')}.xlsx"
        return vendor_dir / filename

    def _cleanup_old_files(self, month_dir: Path, current_timestamp: datetime):
        """
        Удаляет старые файлы, оставляя только последний в каждом периоде.

        Args:
            month_dir: Директория месяца
            current_timestamp: Текущая временная метка
        """
        if not month_dir.exists():
            return

        period = self._get_period(current_timestamp.day)
        files = self._get_period_files(month_dir, period)

        # Оставляем только последний файл в периоде
        if len(files) >= self.MAX_FILES_PER_PERIOD:
            files_to_delete = sorted(files)[:-self.MAX_FILES_PER_PERIOD + 1]
            for file_path in files_to_delete:
                logger.info(f"Удаляем старый файл: {file_path.name}")
                file_path.unlink()

    def _get_period(self, day: int) -> str:
        """Определяет период месяца по дню."""
        if self.PERIOD_START[0] <= day <= self.PERIOD_START[1]:
            return 'start'
        elif self.PERIOD_MID[0] <= day <= self.PERIOD_MID[1]:
            return 'mid'
        else:
            return 'end'

    def _get_period_files(self, month_dir: Path, period: str) -> List[Path]:
        """Получает список файлов для указанного периода."""
        all_files = list(month_dir.glob('*.xlsx')) + list(month_dir.glob('*.xls'))

        period_files = []
        for file_path in all_files:
            file_day = self._extract_day_from_filename(file_path)
            if file_day and self._get_period(file_day) == period:
                period_files.append(file_path)

        return sorted(period_files, key=lambda p: p.stat().st_mtime)

    def _extract_day_from_filename(self, file_path: Path) -> int:
        """Извлекает день из имени файла."""
        try:
            # Формат: VENDOR_price_YYYYMMDD_HHMM.xlsx
            parts = file_path.stem.split('_')
            if len(parts) >= 3:
                date_str = parts[2]  # YYYYMMDD
                if len(date_str) == 8:
                    return int(date_str[6:8])
        except (ValueError, IndexError):
            pass
        return None

    def cleanup_old_months(self, vendor: str, keep_months: int = 3):
        """
        Удаляет директории старше указанного количества месяцев.

        Args:
            vendor: Название вендора
            keep_months: Количество месяцев для хранения (по умолчанию 3)
        """
        vendor_dir = self.base_dir / vendor
        if not vendor_dir.exists():
            return

        current_month = datetime.now().strftime('%Y-%m')

        for month_dir in vendor_dir.iterdir():
            if not month_dir.is_dir():
                continue

            # Проверяем возраст директории
            if self._is_old_month(month_dir.name, current_month, keep_months):
                logger.info(f"Удаляем старую директорию: {month_dir}")
                self._remove_directory(month_dir)

    def _is_old_month(self, month_str: str, current_month: str, keep_months: int) -> bool:
        """Проверяет, является ли месяц старым."""
        try:
            month_date = datetime.strptime(month_str, '%Y-%m')
            current_date = datetime.strptime(current_month, '%Y-%m')

            # Разница в месяцах
            months_diff = (current_date.year - month_date.year) * 12
            months_diff += current_date.month - month_date.month

            return months_diff > keep_months
        except ValueError:
            return False

    def _remove_directory(self, directory: Path):
        """Рекурсивно удаляет директорию."""
        for item in directory.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                self._remove_directory(item)
        directory.rmdir()
