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

    def sync_vendor(self, vendor: str, mark_disappeared: bool = True) -> SyncResult:
        """
        Синхронизирует прайс-лист одного вендора.

        Args:
            vendor: Название вендора
            mark_disappeared: Помечать ли отсутствующие позиции как disappeared.
                             False для ручных загрузок (частичные прайсы).

        Returns:
            SyncResult: Результат синхронизации
        """
        result = SyncResult(vendor=vendor, success=False)

        try:
            logger.info(f"🚀 Начинаем синхронизацию {vendor} (mark_disappeared={mark_disappeared})")

            # 1. Загружаем файл
            try:
                file_path = self.downloader.download(vendor)
            except Exception as download_error:
                try:
                    file_path = self.downloader.storage.get_latest_file(vendor)
                    logger.warning(f"⚠️ {vendor}: загрузка не удалась ({download_error}), использую кэш: {file_path.name}")
                except (FileNotFoundError, AttributeError):
                    raise download_error
            result.file_path = str(file_path)
            logger.info(f"📥 Файл загружен: {file_path.name}")

            # 2. Парсим файл
            new_items = self.parser.parse(file_path, vendor)
            result.total_items = len(new_items)
            logger.info(f"📊 Распарсено {len(new_items)} позиций")

            # 3. Получаем текущие данные из БД (активные + disappeared)
            current_items = self.repository.get_items_by_vendor(vendor)

            # Защита: если парсер вернул 0 позиций, а в БД есть активные —
            # скорее всего файл в другом формате, пропускаем синхронизацию
            if len(new_items) == 0 and len(current_items) > 0:
                result.error_message = (
                    f"Парсер вернул 0 позиций при {len(current_items)} в БД. "
                    f"Возможно файл в неизвестном формате. Синхронизация пропущена."
                )
                result.disappeared_items = 0
                result.success = True
                logger.warning(
                    f"⚠️ {vendor}: парсер вернул 0 позиций, в БД {len(current_items)} — "
                    f"синхронизация пропущена (защита от потери данных)"
                )
                return result
            current_articles = {item.article for item in current_items}
            current_items_map = {item.article: item for item in current_items}

            # Получаем disappeared позиции — они могут вернуться в файле
            disappeared_articles = self.repository.get_disappeared_articles(vendor)
            disappeared_items_map = {item.article: item for item in self.repository.get_disappeared_items(vendor)}
            if disappeared_articles:
                logger.info(f"👻 В БД {len(disappeared_articles)} disappeared позиций")

            # Снятые с производства — не трогаем при синхронизации
            discontinued_articles = self.repository.get_discontinued_articles(vendor)
            if discontinued_articles:
                logger.info("[DISCONTINUED] пропускаем при синхронизации: %d позиций", len(discontinued_articles))

            # 4. Анализируем изменения
            new_items_map = {item.article: item for item in new_items}
            new_articles = set(new_items_map.keys())

            # Отфильтровываем заказные позиции (с ценой 0) для добавления/обновления
            new_items_with_price = [item for item in new_items if float(item.price) > 0]
            new_items_with_price_map = {item.article: item for item in new_items_with_price}
            new_articles_with_price = set(new_items_with_price_map.keys())

            # Все артикулы, которые были в БД (активные + disappeared)
            all_db_articles = current_articles | disappeared_articles

            # Позиции "по запросу" (price=0): разбиваем на новые и существующие
            price_on_request_items = [item for item in new_items if float(item.price) == 0]
            price_on_request_new = [i for i in price_on_request_items if i.article not in all_db_articles]
            price_on_request_existing = [i for i in price_on_request_items if i.article in current_articles]

            # Восстановление disappeared позиций, которые появились в файле
            to_restore = []
            restored_articles = set()
            if disappeared_articles:
                for article in (disappeared_articles & new_articles):
                    restored_articles.add(article)
                    # Если позиция с ценой > 0 — восстанавливаем с новыми данными
                    if article in new_items_with_price_map:
                        to_restore.append(new_items_with_price_map[article])
                    else:
                        # Цена 0 — просто восстанавливаем статус
                        to_restore.append(disappeared_items_map.get(article) or new_items_map[article])

            # Новые позиции (есть в файле, нет в БД нигде — ни active, ни disappeared)
            to_add = [
                new_items_with_price_map[art]
                for art in (new_articles_with_price - all_db_articles)
            ]

            # Исчезнувшие позиции (есть в active БД, нет в файле ВООБЩЕ)
            disappeared_articles = list(current_articles - new_articles)
            disappeared_items = [
                current_items_map[art]
                for art in disappeared_articles
            ]

            # Debug: логируем статистику
            logger.info(f"📊 Статистика: БД(active)={len(current_articles)}, "
                       f"БД(disappeared)={len(disappeared_articles)}, "
                       f"Файл={len(new_articles)}, "
                       f"Восстановлено={len(restored_articles)}, "
                       f"Новых={len(to_add)}, "
                       f"Исчезло={len(disappeared_articles)}")

            # Исчезнувшие позиции — discontinued не трогаем
            if discontinued_articles:
                before = len(disappeared_articles)
                disappeared_articles = [a for a in disappeared_articles if a not in discontinued_articles]
                disappeared_items = [current_items_map[a] for a in disappeared_articles]
                logger.debug("[DISCONTINUED] filtered disappeared: было %d стало %d", before, len(disappeared_articles))

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

            # Восстановление disappeared позиций
            if to_restore:
                restored_count = self.repository.restore_disappeared(
                    vendor, [item.article for item in to_restore]
                )
                # Обновляем данные восстановленных позиций
                if restored_count > 0:
                    items_to_update = [item for item in to_restore if float(item.price) > 0]
                    if items_to_update:
                        self.repository.update_items(items_to_update, synonyms_map=synonyms_map)
                    result.restored_items = restored_count
                    logger.info(f"♻️ Восстановлено из disappeared: {restored_count}")

            if to_add:
                added = self.repository.add_items(to_add, synonyms_map=synonyms_map)
                result.new_items = added
                result.added_items = to_add
                logger.info(f"➕ Добавлено новых: {added}")

            # Discontinued — не обновляем цену/описание
            if discontinued_articles and to_update:
                before = len(to_update)
                to_update = [item for item in to_update if item.article not in discontinued_articles]
                logger.debug("[DISCONTINUED] filtered to_update: было %d стало %d", before, len(to_update))

            if to_update:
                updated = self.repository.update_items(to_update, synonyms_map=synonyms_map)
                result.updated_items = updated
                result.updated_items_list = to_update
                result.price_changes_list = price_changes
                logger.info(f"🔄 Обновлено цен: {updated} (из них изменений цен: {len(price_changes)})")

            # ArticlePC: проставляем код 1С для новых и обновлённых позиций,
            # если парсер его вернул. bulk_set_article_pc перезаписывает
            # ТОЛЬКО пустые ArticlePC (WHERE ArticlePC IS NULL OR = ''),
            # так что уже заполненные коды 1С не затираются.
            items_with_pc = [i for i in to_add + to_update if i.code_1c]
            if items_with_pc and hasattr(self.repository, 'bulk_set_article_pc'):
                pc_payload = [
                    {'vendor': i.vendor, 'part_num': i.article, 'article_pc': i.code_1c}
                    for i in items_with_pc
                ]
                try:
                    pc_updated = self.repository.bulk_set_article_pc(pc_payload)
                    logger.info(
                        "🏷 ArticlePC обновлён для %s/%s позиций",
                        pc_updated, len(items_with_pc),
                    )
                except Exception as e:
                    logger.warning("⚠️ Не удалось обновить ArticlePC: %s", e)

            # Позиции "по запросу": новые добавляем, существующим обновляем PriceText
            if price_on_request_new:
                self.repository.add_items(price_on_request_new, synonyms_map=synonyms_map)
                logger.info(f"💬 Добавлено 'Цена по запросу' (новых): {len(price_on_request_new)}")
            if price_on_request_existing and hasattr(self.repository, 'mark_price_on_request'):
                # Обновляем PriceText и Price
                self.repository.mark_price_on_request(vendor, [i.article for i in price_on_request_existing])
                logger.info(f"💬 Обновлено 'Цена по запросу' (существующих): {len(price_on_request_existing)}")

                # Обновляем описание если изменилось
                items_to_update_descr = []
                for item in price_on_request_existing:
                    old_item = current_items_map.get(item.article)
                    if old_item and (item.description or '') != (old_item.description or ''):
                        items_to_update_descr.append(item)
                if items_to_update_descr:
                    self.repository.update_items(items_to_update_descr, synonyms_map=synonyms_map)
                    logger.info(f"📝 Обновлено описание для {len(items_to_update_descr)} 'Цена по запросу'")

            if disappeared_articles and mark_disappeared:
                marked = self.repository.mark_as_disappeared(vendor, disappeared_articles)
                result.disappeared_items = marked
                result.disappeared_items_list = disappeared_items
                logger.info(f"👻 Помечено исчезнувших: {marked}")
            elif disappeared_articles and not mark_disappeared:
                logger.info(
                    f"📋 Пропуск пометки disappeared: {len(disappeared_articles)} позиций "
                    f"(mark_disappeared=False, ручная загрузка)"
                )

            # 6. Очищаем старые исчезнувшие (только при полной синхронизации)
            if mark_disappeared:
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

            # 9. Автоматически заполняем VendorForFilter для новых/обновлённых записей
            try:
                if hasattr(self.repository, 'backfill_vendor_for_filter'):
                    vff_count = self.repository.backfill_vendor_for_filter(synonyms_map)
                    if vff_count:
                        logger.info(f"🏷 VendorForFilter заполнен для {vff_count} записей")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка backfill VendorForFilter: {e}")

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
