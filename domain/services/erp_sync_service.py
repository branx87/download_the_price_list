import logging
from collections import Counter
from typing import List

from adapters.erp.erp_client import ErpClient
from adapters.database.sql_repository import SqlRepository
from config.settings import settings
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

            # 2. Фильтруем по типу номенклатуры (ERP_SKIP_ITEM_TYPES)
            skip_types = settings.ERP_SKIP_ITEM_TYPES
            if skip_types:
                before = len(raw_items)
                raw_items = [i for i in raw_items if i.get('item_type', '') not in skip_types]
                skipped = before - len(raw_items)
                if skipped:
                    logger.info(f"[ERP] Пропущено по типу номенклатуры: {skipped} из {before}")

            # 3. Проверяем дубликаты по code
            unique_items, duplicate_codes = self._check_duplicates(raw_items)
            result.duplicate_codes = duplicate_codes
            result.skipped_duplicates = len(raw_items) - len(unique_items)

            if duplicate_codes:
                logger.warning(f"Обнаружено {len(duplicate_codes)} дублирующихся кодов ArticlePC")
                for code in duplicate_codes[:10]:
                    logger.warning(f"  Дубликат: {code}")
                if len(duplicate_codes) > 10:
                    logger.warning(f"  ... и ещё {len(duplicate_codes) - 10}")

            # 4. Загружаем маппинг синонимов для VendorForFilter
            # Ключи нормализуем в uppercase — иначе vendor_norm (всегда upper) не найдёт
            # синоним с raw-ключом типа 'Овен' или 'КМ-профиль'
            synonyms_map = {}
            if hasattr(self.repository, 'get_synonyms_cached'):
                raw_synonyms = self.repository.get_synonyms_cached()
                synonyms_map = {
                    DataNormalizer.normalize_vendor_name(k): v
                    for k, v in raw_synonyms.items()
                }
                logger.debug(f"[FIX] synonyms_map загружен: {len(synonyms_map)} записей (ключи нормализованы)")

            # 4. Загружаем существующие данные из БД для быстрого поиска
            logger.debug("Загрузка существующих ArticlePC из БД...")
            existing_article_pcs = self.repository.get_all_article_pcs()
            logger.debug(f"В БД найдено {len(existing_article_pcs)} ArticlePC")

            logger.debug("Загрузка существующих пар Vendor+Part_Num из БД...")
            existing_pairs = self.repository.get_all_vendor_part_num_pairs()
            logger.debug(f"В БД найдено {len(existing_pairs)} пар Vendor+Part_Num")

            # Строим lookup с учётом синонимов:
            # ключ   = (norm_vendor, norm_part_num) — для поиска по нормализованным данным из 1C
            # значение = set[(orig_vendor, orig_part_num, article_pc_upper)] — оригиналы из БД для UPDATE
            #
            # Важно: Part_Num хранится в БД как есть (с пробелами, напр. 'ШМТ осн 80х8').
            # Нормализация используется ТОЛЬКО для сравнения/поиска, но не меняет значения в БД.
            # pair_to_db_vendors: ключ = (norm_vendor, norm_part_num)
            # значение = set[(orig_vendor, orig_part_num, article_pc_upper)]
            # article_pc_upper — уже привязанный ArticlePC (или '' если не привязан)
            pair_to_db_vendors: dict = {}
            synonyms_resolved = 0
            for vendor_raw, part_num_raw, article_pc_upper, _apc_raw in existing_pairs:
                vendor_norm = DataNormalizer.normalize_vendor_name(vendor_raw)
                part_num_norm = DataNormalizer.normalize_article(part_num_raw)
                # Прямой ключ
                key = (vendor_norm, part_num_norm)
                if key not in pair_to_db_vendors:
                    pair_to_db_vendors[key] = set()
                pair_to_db_vendors[key].add((vendor_raw, part_num_raw, article_pc_upper))
                # Ключ по canonical имени синонима (нормализованный)
                # Нужен для случаев когда ERP шлёт vendor под именем VendorForFilter,
                # а в БД хранится под другим именем (напр. 'IKEM' в БД, 'IEK' в 1C)
                if vendor_norm in synonyms_map:
                    canonical_raw = synonyms_map[vendor_norm]
                    canonical_norm = DataNormalizer.normalize_vendor_name(canonical_raw)
                    if canonical_norm != vendor_norm:  # Добавляем только если canonical реально отличается
                        canon_key = (canonical_norm, part_num_norm)
                        if canon_key not in pair_to_db_vendors:
                            pair_to_db_vendors[canon_key] = set()
                            synonyms_resolved += 1
                        pair_to_db_vendors[canon_key].add((vendor_raw, part_num_raw, article_pc_upper))
            if synonyms_resolved:
                logger.info(
                    f"[ERP] Lookup с синонимами: {len(pair_to_db_vendors)} ключей "
                    f"(+{synonyms_resolved} через синонимы из {len(existing_pairs)} пар в БД)"
                )

            # Lookup article_pc_upper → (vendor_raw, part_num_raw, article_pc_raw)
            # article_pc_raw — реально хранимое значение в БД (WHERE ключ для UPDATE)
            article_pc_to_db_row = {
                apc_upper: (v, p, apc_raw)
                for v, p, apc_upper, apc_raw in existing_pairs
                if apc_upper
            }

            # 4. Классифицируем позиции
            to_insert = []
            to_update_article_pc = []
            to_update_raw_values = []
            total_items = len(unique_items)

            for idx, item in enumerate(unique_items):
                try:
                    code_raw = (item.get('code') or '').strip()
                    code_upper = code_raw.upper()  # Только для сравнения/поиска — не для хранения
                    manufacturer_raw = (item.get('manufacturer') or '').strip()
                    manufacturer = DataNormalizer.normalize_vendor_name(manufacturer_raw)
                    article_raw = (item.get('article') or '').strip()
                    article = DataNormalizer.normalize_article(article_raw)

                    # Разрешаем производителя через таблицу синонимов (напр.: КЭАЗ → KEAZ).
                    # synonyms_map содержит нормализованные ключи, поэтому ищем по manufacturer.
                    canonical_raw = synonyms_map.get(manufacturer)
                    if canonical_raw:
                        canonical_norm = DataNormalizer.normalize_vendor_name(canonical_raw)
                        logger.debug(
                            f"[ERP] Синоним производителя: '{manufacturer_raw}' -> '{canonical_raw}'"
                        )
                        manufacturer_raw = canonical_raw
                        manufacturer = canonical_norm
                    name = (item.get('name') or '').strip()
                    unit = (item.get('unit') or 'шт').strip()

                    if manufacturer_raw != manufacturer:
                        logger.debug(f"[ERP] Нормализация производителя: '{manufacturer_raw}' -> '{manufacturer}'")
                    if article_raw != article:
                        logger.debug(f"[ERP] Нормализация артикула: '{article_raw}' -> '{article}'")
                    if code_raw != code_upper:
                        logger.debug(f"[ERP] Нормализация кода (только для поиска): '{code_raw}' -> '{code_upper}'")

                    if not code_raw or not manufacturer or not article:
                        logger.debug(f"Пропуск позиции без обязательных полей: code={code_raw}, "
                                    f"manufacturer={manufacturer}, article={article}")
                        result.errors += 1
                        result.error_details.append(
                            f"Пустые обязательные поля: code={code_raw}, manufacturer={manufacturer}, article={article}"
                        )
                        continue

                    # Шаг 1: ArticlePC уже есть в БД → пропускаем, но обновляем raw значения
                    if code_upper in existing_article_pcs:
                        db_row = article_pc_to_db_row.get(code_upper)
                        if db_row:
                            db_vendor, db_part_num, stored_article_pc = db_row
                            needs_vendor_update = db_vendor != manufacturer_raw or db_part_num != article_raw
                            needs_case_fix = stored_article_pc != code_raw
                            if needs_vendor_update or needs_case_fix:
                                update_item = {
                                    'vendor': manufacturer_raw,
                                    'part_num': article_raw,
                                    'article_pc': stored_article_pc,  # WHERE ключ = реально хранимое
                                    'descr': name,
                                }
                                if needs_case_fix:
                                    update_item['new_article_pc'] = code_raw
                                    logger.debug(
                                        f"[FIX] Исправление регистра ArticlePC: "
                                        f"'{stored_article_pc}' -> '{code_raw}'"
                                    )
                                to_update_raw_values.append(update_item)
                        # Дополнительная проверка: есть ли строки с тем же нормализованным
                        # vendor+Part_Num, но без ArticlePC (дубль с другим регистром вендора)?
                        # Например: 'Овен/107381/УП-00515903' уже в БД,
                        # но 'ОВЕН/107381/' (прайс-файл) остался незалинкованным.
                        norm_key = (manufacturer, article)
                        if norm_key in pair_to_db_vendors:
                            for dup_vendor, dup_part_num, dup_apc in pair_to_db_vendors[norm_key]:
                                if not dup_apc:  # Строка без ArticlePC — нужно привязать
                                    to_update_article_pc.append({
                                        'vendor': dup_vendor,
                                        'part_num': dup_part_num,
                                        'article_pc': code_raw,
                                    })
                                    logger.info(
                                        f"[FIX] Привязка дубля к прайс-файловой строке: "
                                        f"vendor={dup_vendor!r} part_num={dup_part_num!r} "
                                        f"article_pc={code_raw!r}"
                                    )
                        result.skipped_existing += 1
                        continue

                    # Шаг 2: Найден по Vendor+Part_Num (с учётом синонимов) → привязываем ArticlePC
                    # Обновляем ВСЕ вендора с таким артикулом (SE + 1SE + A-SE)
                    if (manufacturer, article) in pair_to_db_vendors:
                        db_pairs = pair_to_db_vendors[(manufacturer, article)]
                        # db_pairs — set[(orig_vendor, orig_part_num, article_pc_upper)] из БД
                        db_vendor_names = {v for v, _, _ in db_pairs}

                        # Если у найденных DB-пар уже есть ArticlePC (любой) → не перезаписываем.
                        # Это предотвращает пинг-понг когда одна позиция имеет 2 кода в 1C:
                        # оба кода поочерёдно перезаписывали бы друг друга.
                        existing_apcs = {apc for _, _, apc in db_pairs if apc}
                        if existing_apcs:
                            # Строки уже привязаны. Добавляем ВСЕ коды (и текущий, и старые)
                            # в existing_article_pcs чтобы оба считались "обработанными"
                            existing_article_pcs.add(code_upper)
                            existing_article_pcs.update(existing_apcs)
                            result.skipped_existing += 1
                            if code_upper not in existing_apcs:
                                logger.debug(
                                    f"[ERP] Пропуск {code_raw!r}: пара уже имеет ArticlePC={sorted(existing_apcs)}"
                                )
                            continue

                        synonym_vendors = db_vendor_names - {manufacturer}
                        if synonym_vendors:
                            logger.debug(
                                f"[ERP] Найдено через синонимы: '{manufacturer}' -> {sorted(synonym_vendors)} | {article}"
                            )
                        for orig_vendor, orig_part_num, orig_apc in db_pairs:
                            # Используем оригинальный Part_Num из БД, чтобы JOIN в bulk_set_article_pc
                            # нашёл запись — даже если Part_Num содержит внутренние пробелы ('ШМТ осн 80х8')
                            logger.debug(
                                f"[ERP] to_update: vendor={orig_vendor!r} part_num={orig_part_num!r} "
                                f"article_pc={code_raw!r}"
                            )
                            to_update_article_pc.append({
                                'vendor': orig_vendor,
                                'part_num': orig_part_num,
                                'article_pc': code_raw
                            })
                        existing_article_pcs.add(code_upper)
                        result.updated += 1
                        result.linked_details.append({
                            'vendor': sorted(db_vendor_names)[0],
                            'part_num': article_raw,
                            'article_pc': code_raw,
                            'name': name
                        })
                        logger.debug(f"[ERP] Привязка ArticlePC: {sorted(db_vendor_names)} | {article} -> код {code_raw!r}")
                        continue

                    # Шаг 3: Нигде не найден → INSERT
                    # Сохраняем ОРИГИНАЛЬНЫЕ значения как есть (нормализация только для поиска)
                    logger.debug(
                        f"[FIX] INSERT: vendor={manufacturer_raw!r} part_num={article_raw!r} "
                        f"(norm: vendor={manufacturer!r} article={article!r}) article_pc={code_raw!r}"
                    )
                    to_insert.append({
                        'vendor': manufacturer_raw,
                        'part_num': article_raw,
                        'descr': name,
                        'units': unit,
                        'article_pc': code_raw,
                        'vendor_for_filter': synonyms_map.get(manufacturer, '1C-ERP')
                    })
                    existing_article_pcs.add(code_upper)
                    pair_to_db_vendors[(manufacturer, article)] = {(manufacturer_raw, article_raw, code_upper)}  # предотвращаем дубли в батче
                    result.added += 1
                    result.added_details.append({
                        'vendor': manufacturer_raw,
                        'part_num': article_raw,
                        'article_pc': code_raw,
                        'name': name
                    })
                    logger.debug(f"[ERP] Новая позиция: {manufacturer} | {article} | {name} | код {code_raw!r}")

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

            if to_update_raw_values:
                logger.info(f"Обновление vendor/part_num до raw значений для {len(to_update_raw_values)} позиций...")
                self.repository.bulk_update_raw_values(to_update_raw_values)

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
