import logging
from typing import List, Optional
from datetime import datetime
from pathlib import Path

from domain.entities.price_item import PriceItem
from domain.entities.sync_result import SyncResult
from domain.entities.price_comparison import PriceComparisonResult, PriceChange
from domain.interfaces.downloader import IDownloader
from domain.interfaces.parser import IParser
from domain.interfaces.repository import IRepository
from domain.services.report_service import ReportService


logger = logging.getLogger(__name__)


class SyncService:
    """
    Сервис синхронизации прайс-листов.

    Принцип работы:
    1. Загружает файл через IDownloader
    2. Парсит файл через IParser
    3. Сравнивает с текущими данными из IRepository
    4. Обновляет БД через IRepository
    """

    def __init__(
        self,
        downloader: IDownloader,
        parser: IParser,
        repository: IRepository,
        price_change_threshold: float = 0.01,
        report_service: Optional[ReportService] = None
    ):
        self.downloader = downloader
        self.parser = parser
        self.repository = repository
        self.price_change_threshold = price_change_threshold
        self.report_service = report_service

    def sync_vendor(self, vendor: str) -> SyncResult:
        """
        Синхронизирует прайс-лист одного вендора.

        Args:
            vendor: Название вендора

        Returns:
            SyncResult: Результат синхронизации
        """
        result = SyncResult(vendor=vendor, success=False)

        try:
            logger.info(f"🚀 Начинаем синхронизацию {vendor}")

            # 1. Загружаем файл
            file_path = self.downloader.download(vendor)
            result.file_path = str(file_path)
            logger.info(f"📥 Файл загружен: {file_path.name}")

            # 2. Парсим файл
            new_items = self.parser.parse(file_path, vendor)
            result.total_items = len(new_items)
            logger.info(f"📊 Распарсено {len(new_items)} позиций")

            # 3. Получаем текущие данные из БД
            current_items = self.repository.get_items_by_vendor(vendor)
            current_articles = {item.article for item in current_items}
            current_items_map = {item.article: item for item in current_items}

            # 4. Анализируем изменения
            new_items_map = {item.article: item for item in new_items}
            new_articles = set(new_items_map.keys())

            # Отфильтровываем заказные позиции (с ценой 0) для добавления/обновления
            new_items_with_price = [item for item in new_items if float(item.price) > 0]
            new_items_with_price_map = {item.article: item for item in new_items_with_price}
            new_articles_with_price = set(new_items_with_price_map.keys())

            # Новые позиции (есть в файле, нет в БД, и есть цена)
            to_add = [
                new_items_with_price_map[art]
                for art in (new_articles_with_price - current_articles)
            ]

            # Исчезнувшие позиции (есть в БД, нет в файле ВООБЩЕ - даже без цены)
            disappeared_articles = list(current_articles - new_articles)
            disappeared_items = [
                current_items_map[art]
                for art in disappeared_articles
            ]

            # Debug: логируем статистику
            logger.info(f"📊 Статистика артикулов: БД={len(current_articles)}, "
                       f"Файл={len(new_articles)}, Исчезло={len(disappeared_articles)}")

            # Обновленные позиции (изменилась цена, и новая цена > 0)
            to_update = []
            for article in (new_articles_with_price & current_articles):
                new_item = new_items_with_price_map[article]
                old_item = current_items_map[article]
                if new_item.has_price_changed(old_item, self.price_change_threshold):
                    to_update.append(new_item)

            # 5. Применяем изменения
            if to_add:
                added = self.repository.add_items(to_add)
                result.new_items = added
                result.added_items = to_add
                logger.info(f"➕ Добавлено новых: {added}")

            if to_update:
                updated = self.repository.update_items(to_update)
                result.updated_items = updated
                result.updated_items_list = to_update
                logger.info(f"🔄 Обновлено цен: {updated}")

            if disappeared_articles:
                marked = self.repository.mark_as_disappeared(vendor, disappeared_articles)
                result.disappeared_items = marked
                result.disappeared_items_list = disappeared_items
                logger.info(f"👻 Помечено исчезнувших: {marked}")

            # 6. Очищаем старые исчезнувшие
            self.repository.delete_old_disappeared(vendor, days=30)

            # 7. Сбрасываем статус price_changed после синхронизации
            reset_count = self.repository.reset_changed_status(vendor)
            if reset_count > 0:
                logger.info(f"🔄 Сброшен статус для {reset_count} позиций")

            # 8. Создаем Excel отчет
            if self.report_service and result.changes_count > 0:
                try:
                    report_path = self.report_service.create_report(vendor, result)
                    logger.info(f"📊 Отчет создан: {report_path.name}")
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось создать отчет: {e}")

            result.success = True
            logger.info(f"✅ Синхронизация {vendor} завершена успешно")

        except Exception as e:
            result.error_message = str(e)
            logger.error(f"❌ Ошибка синхронизации {vendor}: {e}", exc_info=True)

        finally:
            result.finished_at = datetime.now()

        return result

    def sync_all_vendors(self, vendors: List[str]) -> List[SyncResult]:
        """
        Синхронизирует все вендоры.

        Args:
            vendors: Список названий вендоров

        Returns:
            List[SyncResult]: Результаты синхронизации
        """
        results = []
        for vendor in vendors:
            result = self.sync_vendor(vendor)
            results.append(result)
        return results

    def check_price_changes(self, vendor: str) -> PriceComparisonResult:
        """
        Проверяет изменения в прайс-листе без обновления БД.

        Args:
            vendor: Название вендора

        Returns:
            PriceComparisonResult: Результат сравнения цен
        """
        result = PriceComparisonResult(vendor=vendor)

        try:
            logger.info(f"Проверка актуальности прайса {vendor}")

            # 1. Загружаем и парсим новый файл
            file_path = self.downloader.download(vendor)
            new_items = self.parser.parse(file_path, vendor)
            result.total_in_file = len(new_items)

            # 2. Получаем текущие данные из БД (активные позиции)
            current_items = self.repository.get_items_by_vendor(vendor)
            # Получаем общее количество включая исчезнувшие
            result.total_in_db = self.repository.get_vendor_total_count(vendor)
            result.last_db_update = self.repository.get_vendor_last_update(vendor)

            # 3. Создаем словари для быстрого поиска
            current_items_map = {item.article: item for item in current_items}
            new_items_map = {item.article: item for item in new_items}

            # Фильтруем позиции с ценой > 0
            new_items_with_price = [item for item in new_items if float(item.price) > 0]
            new_items_with_price_map = {item.article: item for item in new_items_with_price}

            current_articles = set(current_items_map.keys())
            new_articles_with_price = set(new_items_with_price_map.keys())
            new_articles = set(new_items_map.keys())

            # 4. Анализируем новые позиции
            new_articles_set = new_articles_with_price - current_articles
            result.new_items = [new_items_with_price_map[art] for art in new_articles_set]
            result.new_items_count = len(result.new_items)

            # 5. Анализируем исчезнувшие позиции
            disappeared_articles = current_articles - new_articles
            result.disappeared_items = [current_items_map[art] for art in disappeared_articles]
            result.disappeared_items_count = len(result.disappeared_items)

            # 6. Анализируем изменения цен
            for article in (new_articles_with_price & current_articles):
                new_item = new_items_with_price_map[article]
                old_item = current_items_map[article]

                if new_item.has_price_changed(old_item, self.price_change_threshold):
                    price_change = PriceChange(
                        article=article,
                        description=new_item.description,
                        old_price=old_item.price,
                        new_price=new_item.price
                    )
                    result.price_changes.append(price_change)

                    # Логируем первые 3 изменения для отладки
                    if len(result.price_changes) <= 3:
                        logger.debug(f"Изменение цены: {article} | "
                                   f"old={old_item.price} | new={new_item.price} | "
                                   f"diff={price_change.price_diff_percent:.1f}%")

            result.updated_items_count = len(result.price_changes)

            logger.info(f"Проверка завершена: новых={result.new_items_count}, "
                       f"изменений={result.updated_items_count}, "
                       f"исчезло={result.disappeared_items_count}")

        except Exception as e:
            logger.error(f"Ошибка при проверке {vendor}: {e}", exc_info=True)

        return result
