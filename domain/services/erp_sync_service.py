import logging
from collections import Counter
from typing import List

from adapters.erp.erp_client import ErpClient
from adapters.database.sql_repository import SqlRepository
from domain.entities.erp_sync_result import ErpSyncResult
from domain.services.data_normalizer import DataNormalizer

logger = logging.getLogger(__name__)


class ErpSyncService:
    """Сервис синхронизации номенклатуры из 1C-ERP"""

    def __init__(self, erp_client: ErpClient, repository: SqlRepository):
        self.erp_client = erp_client
        self.repository = repository

    def sync_from_erp(self) -> ErpSyncResult:
        """
        Загрузка номенклатуры из 1C-ERP.

        Логика:
        1. Получаем все позиции из API
        2. Проверяем дубликаты по code (ArticlePC) — логируем, пропускаем
        3. Для каждой уникальной позиции:
           - Если ArticlePC уже есть в БД → пропускаем
           - Если найден по Vendor+Part_Num → проставляем ArticlePC
           - Иначе → INSERT новую запись
        """
        result = ErpSyncResult()

        try:
            # 1. Загружаем данные из 1C
            raw_items = self.erp_client.fetch_nomenclature()
            result.total_received = len(raw_items)
            logger.info(f"Получено {len(raw_items)} позиций из 1C-ERP")

            # 2. Проверяем дубликаты по code
            unique_items, duplicate_codes = self._check_duplicates(raw_items)
            result.duplicate_codes = duplicate_codes
            result.skipped_duplicates = len(raw_items) - len(unique_items)

            if duplicate_codes:
                logger.warning(f"Обнаружено {len(duplicate_codes)} дублирующихся кодов ArticlePC")
                for code in duplicate_codes[:10]:
                    logger.warning(f"  Дубликат: {code}")
                if len(duplicate_codes) > 10:
                    logger.warning(f"  ... и ещё {len(duplicate_codes) - 10}")

            # 3. Загружаем маппинг синонимов для VendorForFilter
            synonyms_map = {}
            if hasattr(self.repository, 'get_synonyms_cached'):
                synonyms_map = self.repository.get_synonyms_cached()

            # 4. Загружаем существующие данные из БД для быстрого поиска
            logger.debug("Загрузка существующих ArticlePC из БД...")
            existing_article_pcs = self.repository.get_all_article_pcs()
            logger.debug(f"В БД найдено {len(existing_article_pcs)} ArticlePC")

            logger.debug("Загрузка существующих пар Vendor+Part_Num из БД...")
            existing_pairs = self.repository.get_all_vendor_part_num_pairs()
            logger.debug(f"В БД найдено {len(existing_pairs)} пар Vendor+Part_Num")

            # Строим lookup с учётом синонимов:
            # ключ   = (norm_vendor, norm_part_num) — для поиска по нормализованным данным из 1C
            # значение = set[(orig_vendor, orig_part_num)] — оригинальные значения из БД для UPDATE
            #
            # Важно: Part_Num хранится в БД как есть (с пробелами, напр. 'ШМТ осн 80х8').
            # Нормализация используется ТОЛЬКО для сравнения/поиска, но не меняет значения в БД.
            pair_to_db_vendors: dict = {}
            synonyms_resolved = 0
            for vendor_raw, part_num_raw in existing_pairs:
                vendor_norm = DataNormalizer.normalize_vendor_name(vendor_raw)
                part_num_norm = DataNormalizer.normalize_article(part_num_raw)
                # Прямой ключ
                key = (vendor_norm, part_num_norm)
                if key not in pair_to_db_vendors:
                    pair_to_db_vendors[key] = set()
                pair_to_db_vendors[key].add((vendor_raw, part_num_raw))
                # Ключ по canonical имени синонима
                if vendor_norm in synonyms_map:
                    canonical = synonyms_map[vendor_norm]
                    canon_key = (canonical, part_num_norm)
                    if canon_key not in pair_to_db_vendors:
                        pair_to_db_vendors[canon_key] = set()
                        synonyms_resolved += 1
                    pair_to_db_vendors[canon_key].add((vendor_raw, part_num_raw))
            if synonyms_resolved:
                logger.info(
                    f"[ERP] Lookup с синонимами: {len(pair_to_db_vendors)} ключей "
                    f"(+{synonyms_resolved} через синонимы из {len(existing_pairs)} пар в БД)"
                )

            # 4. Классифицируем позиции
            to_insert = []
            to_update_article_pc = []
            total_items = len(unique_items)

            for idx, item in enumerate(unique_items):
                try:
                    code_raw = (item.get('code') or '').strip()
                    code = code_raw.upper()  # Нормализуем для сравнения с БД
                    manufacturer_raw = (item.get('manufacturer') or '').strip()
                    manufacturer = DataNormalizer.normalize_vendor_name(manufacturer_raw)
                    article_raw = (item.get('article') or '').strip()
                    article = DataNormalizer.normalize_article(article_raw)
                    name = (item.get('name') or '').strip()
                    unit = (item.get('unit') or 'шт').strip()

                    if manufacturer_raw != manufacturer:
                        logger.debug(f"[ERP] Нормализация производителя: '{manufacturer_raw}' -> '{manufacturer}'")
                    if article_raw != article:
                        logger.debug(f"[ERP] Нормализация артикула: '{article_raw}' -> '{article}'")
                    if code_raw != code:
                        logger.debug(f"[ERP] Нормализация кода: '{code_raw}' -> '{code}'")

                    if not code or not manufacturer or not article:
                        logger.debug(f"Пропуск позиции без обязательных полей: code={code}, "
                                    f"manufacturer={manufacturer}, article={article}")
                        result.errors += 1
                        result.error_details.append(
                            f"Пустые обязательные поля: code={code}, manufacturer={manufacturer}, article={article}"
                        )
                        continue

                    # Шаг 1: ArticlePC уже есть в БД → пропускаем
                    if code in existing_article_pcs:
                        result.skipped_existing += 1
                        continue

                    # Шаг 2: Найден по Vendor+Part_Num (с учётом синонимов) → привязываем ArticlePC
                    # Обновляем ВСЕ вендора с таким артикулом (SE + 1SE + A-SE)
                    if (manufacturer, article) in pair_to_db_vendors:
                        db_pairs = pair_to_db_vendors[(manufacturer, article)]
                        # db_pairs — set[(orig_vendor, orig_part_num)] из БД
                        db_vendor_names = {v for v, _ in db_pairs}
                        synonym_vendors = db_vendor_names - {manufacturer}
                        if synonym_vendors:
                            logger.debug(
                                f"[ERP] Найдено через синонимы: '{manufacturer}' -> {sorted(synonym_vendors)} | {article}"
                            )
                        for orig_vendor, orig_part_num in db_pairs:
                            # Используем оригинальный Part_Num из БД, чтобы JOIN в bulk_set_article_pc
                            # нашёл запись — даже если Part_Num содержит внутренние пробелы ('ШМТ осн 80х8')
                            logger.debug(
                                f"[FIX] to_update: vendor={orig_vendor!r} part_num={orig_part_num!r} "
                                f"(norm: {article!r}) article_pc={code!r}"
                            )
                            to_update_article_pc.append({
                                'vendor': orig_vendor,
                                'part_num': orig_part_num,
                                'article_pc': code
                            })
                        existing_article_pcs.add(code)
                        result.updated += 1
                        result.linked_details.append({
                            'vendor': sorted(db_vendor_names)[0],
                            'part_num': article,
                            'article_pc': code,
                            'name': name
                        })
                        logger.debug(f"[ERP] Привязка ArticlePC: {sorted(db_vendor_names)} | {article} -> код {code}")
                        continue

                    # Шаг 3: Нигде не найден → INSERT
                    to_insert.append({
                        'vendor': manufacturer,
                        'part_num': article,
                        'descr': name,
                        'units': unit,
                        'article_pc': code,
                        'vendor_for_filter': synonyms_map.get(manufacturer, '1C-ERP')
                    })
                    existing_article_pcs.add(code)
                    pair_to_db_vendors[(manufacturer, article)] = {manufacturer}  # предотвращаем дубли в батче
                    result.added += 1
                    result.added_details.append({
                        'vendor': manufacturer,
                        'part_num': article,
                        'article_pc': code,
                        'name': name
                    })
                    logger.debug(f"[ERP] Новая позиция: {manufacturer} | {article} | {name} | код {code}")

                except Exception as e:
                    result.errors += 1
                    result.error_details.append(f"Ошибка обработки позиции {item.get('code', '?')}: {e}")
                    logger.error(f"Ошибка обработки позиции: {e}", exc_info=True)

                # Прогресс каждые 5000 позиций
                if (idx + 1) % 5000 == 0:
                    logger.info(
                        f"[ERP] Классификация: {idx + 1}/{total_items} — "
                        f"новых: {result.added}, привязок: {result.updated}, пропущено: {result.skipped_existing}"
                    )

            # 5. Применяем изменения в БД
            if to_update_article_pc:
                logger.info(f"Обновление ArticlePC для {len(to_update_article_pc)} позиций...")
                self.repository.bulk_set_article_pc(to_update_article_pc)

            if to_insert:
                logger.info(f"Добавление {len(to_insert)} новых позиций из 1C-ERP...")
                self.repository.add_erp_items(to_insert)

            logger.info(
                f"Синхронизация 1C-ERP завершена: "
                f"получено={result.total_received}, "
                f"добавлено={result.added}, "
                f"обновлено_ArticlePC={result.updated}, "
                f"пропущено_существующих={result.skipped_existing}, "
                f"дубликатов={result.skipped_duplicates}, "
                f"ошибок={result.errors}"
            )

        except Exception as e:
            logger.error(f"Критическая ошибка синхронизации 1C-ERP: {e}", exc_info=True)
            result.errors += 1
            result.error_details.append(f"Критическая ошибка: {e}")

        return result

    def _check_duplicates(self, items: List[dict]) -> tuple:
        """
        Проверяет дубликаты по полю code.

        Returns:
            (unique_items, duplicate_codes)
        """
        code_counts = Counter(item.get('code', '') for item in items)
        duplicate_codes = [code for code, count in code_counts.items() if count > 1 and code]

        duplicate_set = set(duplicate_codes)
        seen_codes = set()
        unique_items = []

        for item in items:
            code = item.get('code', '')
            if code in duplicate_set:
                # Для дубликатов берём только первое вхождение
                if code not in seen_codes:
                    unique_items.append(item)
                    seen_codes.add(code)
            else:
                unique_items.append(item)
                seen_codes.add(code)

        return unique_items, duplicate_codes
