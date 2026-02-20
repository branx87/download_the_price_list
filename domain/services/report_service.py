"""Сервис для создания отчётов об изменениях"""
from pathlib import Path
from datetime import datetime, timedelta
import logging
import pandas as pd
from typing import List
from domain.entities.sync_result import SyncResult
from domain.entities.erp_sync_result import ErpSyncResult

logger = logging.getLogger(__name__)


class ReportService:
    """Создаёт Excel отчёты об изменениях прайсов"""

    def __init__(self, reports_dir: Path):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(exist_ok=True, parents=True)

    def cleanup_old_reports(self, days: int = 7) -> int:
        """Удаляет отчёты старше указанного количества дней. Возвращает число удалённых файлов."""
        cutoff = datetime.now() - timedelta(days=days)
        deleted = 0
        for f in self.reports_dir.glob("report_*.xlsx"):
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                try:
                    f.unlink()
                    deleted += 1
                except Exception as e:
                    logger.warning(f"[REPORT] Не удалось удалить {f.name}: {e}")
        if deleted:
            logger.info(f"[REPORT] Удалено старых отчётов: {deleted}")
        return deleted

    def create_report(self, vendor: str, result: SyncResult) -> Path:
        """
        Создаёт Excel отчёт с листами: новые/удалённые/изменения цен/сводка.
        Перед созданием удаляет отчёты старше 7 дней.

        Args:
            vendor: Название вендора
            result: Результат синхронизации

        Returns:
            Path к созданному файлу
        """
        self.cleanup_old_reports(days=7)

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

            # Лист 3: Изменения цен (старая vs новая)
            if result.price_changes_list:
                self._write_price_changes_sheet(writer, result.price_changes_list)

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

    def _write_price_changes_sheet(self, writer, price_changes: List):
        """Записывает лист с изменениями цен: артикул, наименование, старая цена, новая цена"""
        data = [
            {
                'Артикул': change.article,
                'Наименование': change.description,
                'Старая цена': float(change.old_price),
                'Новая цена': float(change.new_price),
                'Изменение, %': f"{change.price_diff_percent:+.1f}%"
            }
            for change in price_changes
        ]
        df = pd.DataFrame(data)
        df.to_excel(writer, sheet_name='Изменения цен', index=False)

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

    def create_erp_report(self, result: ErpSyncResult) -> Path:
        """
        Создаёт Excel отчёт по результатам ERP-синхронизации.

        Листы: Добавленные, Привязанные ArticlePC, Сводка.
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"report_ERP_{timestamp}.xlsx"
        filepath = self.reports_dir / filename

        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            if result.added_details:
                data = [
                    {
                        'Производитель': item['vendor'],
                        'Артикул': item['part_num'],
                        'Наименование': item['name'],
                        'Код 1C (ArticlePC)': item['article_pc'],
                    }
                    for item in result.added_details
                ]
                pd.DataFrame(data).to_excel(writer, sheet_name='Добавленные', index=False)

            if result.linked_details:
                data = [
                    {
                        'Производитель': item['vendor'],
                        'Артикул': item['part_num'],
                        'Наименование': item.get('name', ''),
                        'Код 1C (ArticlePC)': item['article_pc'],
                    }
                    for item in result.linked_details
                ]
                pd.DataFrame(data).to_excel(writer, sheet_name='Привязанные ArticlePC', index=False)

            summary = {
                'Параметр': [
                    'Получено из 1C',
                    'Добавлено новых',
                    'Привязано ArticlePC',
                    'Пропущено (уже есть)',
                    'Дубликатов кодов',
                    'Ошибок',
                ],
                'Значение': [
                    result.total_received,
                    result.added,
                    result.updated,
                    result.skipped_existing,
                    result.skipped_duplicates,
                    result.errors,
                ]
            }
            pd.DataFrame(summary).to_excel(writer, sheet_name='Сводка', index=False)

        return filepath
