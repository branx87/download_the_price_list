"""Сервис для создания отчётов об изменениях"""
from pathlib import Path
from datetime import datetime
import pandas as pd
from typing import List
from domain.entities.sync_result import SyncResult


class ReportService:
    """Создаёт Excel отчёты об изменениях прайсов"""

    def __init__(self, reports_dir: Path):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(exist_ok=True, parents=True)

    def create_report(self, vendor: str, result: SyncResult) -> Path:
        """
        Создаёт Excel отчёт с 3 листами: новые/удалённые/изменённые позиции.

        Args:
            vendor: Название вендора
            result: Результат синхронизации

        Returns:
            Path к созданному файлу
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"report_{vendor}_{timestamp}.xlsx"
        filepath = self.reports_dir / filename

        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            # Лист 1: Новые позиции
            if result.added_items:
                self._write_items_sheet(writer, result.added_items, 'Новые')

            # Лист 2: Удалённые позиции
            if result.disappeared_items_list:
                self._write_items_sheet(writer, result.disappeared_items_list, 'Удалённые')

            # Лист 3: Изменённые цены
            if result.updated_items:
                self._write_items_sheet(writer, result.updated_items, 'Изменённые')

            # Сводка
            self._write_summary_sheet(writer, result)

        return filepath

    def _write_items_sheet(self, writer, items: List, sheet_name: str):
        """Записывает позиции в лист Excel"""
        data = [
            {
                'Артикул': item.article,
                'Наименование': item.description,
                'Цена': item.price,
                'Единицы': item.units
            }
            for item in items
        ]
        df = pd.DataFrame(data)
        df.to_excel(writer, sheet_name=sheet_name, index=False)

    def _write_summary_sheet(self, writer, result: SyncResult):
        """Записывает сводку в отдельный лист"""
        summary = {
            'Параметр': [
                'Всего позиций',
                'Новых',
                'Обновлено',
                'Исчезло',
                'Статус',
                'Время выполнения'
            ],
            'Значение': [
                result.total_items,
                result.new_items,
                result.updated_items,
                result.disappeared_items,
                'Успешно' if result.success else 'Ошибка',
                f"{result.execution_time:.1f} сек"
            ]
        }
        df = pd.DataFrame(summary)
        df.to_excel(writer, sheet_name='Сводка', index=False)
