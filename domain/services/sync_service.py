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

            # Обновленные позиции (изменилась цена или описание, и новая цена > 0)
            to_update = []
            price_changes = []
            for article in (new_articles_with_price & current_articles):
                new_item = new_items_with_price_map[article]
                old_item = current_items_map[article]
                price_changed = new_item.has_price_changed(old_item, self.price_change_threshold)
                desc_changed = (new_item.description or '') != (old_item.description or '')
                if price_changed or desc_changed:
                    to_update.append(new_item)
                    if price_changed:
                        price_changes.append(PriceChange(
                            article=article,
                            description=new_item.description,
                            old_price=old_item.price,
                            new_price=new_item.price
                        ))

            # 5. Загружаем маппинг синонимов для VendorForFilter
            synonyms_map = {}
            if hasattr(self.repository, 'get_synonyms_cached'):
                synonyms_map = self.repository.get_synonyms_cached()

            # 6. Применяем изменения
            if to_add:
                added = self.repository.add_items(to_add, synonyms_map=synonyms_map)
                result.new_items = added
                result.added_items = to_add
                logger.info(f"➕ Добавлено новых: {added}")

            if to_update:
                updated = self.repository.update_items(to_update, synonyms_map=synonyms_map)
                result.updated_items = updated
                result.updated_items_list = to_update
                result.price_changes_list = price_changes
                logger.info(f"🔄 Обновлено цен: {updated} (из них изменений цен: {len(price_changes)})")

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

    def check_price_changes(self, vendor: str, use_cached: bool = True) -> PriceComparisonResult:
        """
        Проверяет изменения в прайс-листе без обновления БД.

        Args:
            vendor: Название вендора
            use_cached: Использовать последний скачанный файл вместо нового (по умолчанию True)

        Returns:
            PriceComparisonResult: Результат сравнения цен
        """
        result = PriceComparisonResult(vendor=vendor)

        try:
            logger.info(f"Проверка актуальности прайса {vendor}")

            # 1. Получаем время последнего обновления БД
            last_db_update = self.repository.get_vendor_last_update(vendor)

            # 2. Загружаем и парсим файл
            if use_cached:
                # Используем последний скачанный файл
                from utils.file_storage import PriceFileStorage
                from config.settings import settings
                from datetime import datetime
                storage = PriceFileStorage(settings.PRICE_FILES_DIR)
                try:
                    latest_file = storage.get_latest_file(vendor)
                    file_mtime = datetime.fromtimestamp(latest_file.stat().st_mtime)

                    # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Проверяем, что файл новее последнего обновления БД
                    if last_db_update and file_mtime <= last_db_update:
                        # Файл старее или равен последнему обновлению БД - нет изменений
                        logger.info(f"📂 Файл ({file_mtime}) не новее БД ({last_db_update}) - изменений нет")
                        result.total_in_file = self.repository.get_vendor_total_count(vendor)
                        result.total_in_db = result.total_in_file
                        result.last_db_update = last_db_update
                        return result

                    file_path = latest_file
                    logger.info(f"📂 Проверка по кэшу (файл {file_mtime})")
                except FileNotFoundError:
                    logger.warning(f"⚠️ Нет кэшированного файла, скачиваю новый")
                    file_path = self.downloader.download(vendor)
            else:
                # Скачиваем новый файл
                file_path = self.downloader.download(vendor)

            new_items = self.parser.parse(file_path, vendor)
            result.total_in_file = len(new_items)

            # 3. Получаем текущие данные из БД (активные позиции)
            current_items = self.repository.get_items_by_vendor(vendor)
            # ИСПРАВЛЕНИЕ: Получаем количество только активных позиций (исключая disappeared)
            result.total_in_db = len(current_items)
            result.last_db_update = last_db_update

            # 4. Создаем словари для быстрого поиска
            current_items_map = {item.article: item for item in current_items}
            new_items_map = {item.article: item for item in new_items}

            # Фильтруем позиции с ценой > 0
            new_items_with_price = [item for item in new_items if float(item.price) > 0]
            new_items_with_price_map = {item.article: item for item in new_items_with_price}

            current_articles = set(current_items_map.keys())
            new_articles_with_price = set(new_items_with_price_map.keys())
            new_articles = set(new_items_map.keys())

            # 5. Анализируем новые позиции
            new_articles_set = new_articles_with_price - current_articles
            result.new_items = [new_items_with_price_map[art] for art in new_articles_set]
            result.new_items_count = len(result.new_items)

            # 6. Анализируем исчезнувшие позиции
            disappeared_articles = current_articles - new_articles
            result.disappeared_items = [current_items_map[art] for art in disappeared_articles]
            result.disappeared_items_count = len(result.disappeared_items)

            # 7. Анализируем изменения цен
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
