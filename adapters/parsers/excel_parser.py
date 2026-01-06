import logging
import pandas as pd
from pathlib import Path
from decimal import Decimal
from typing import List, Dict, Any
import re

from domain.interfaces.parser import IParser
from domain.entities.price_item import PriceItem
from utils.normalizer import ArticleNormalizer
from domain.services.data_normalizer import DataNormalizer


logger = logging.getLogger(__name__)


class ExcelParser(IParser):
    """
    Универсальный парсер Excel файлов с конфигурацией под вендора.

    Конфигурация определяет:
    - Какие колонки искать
    - Какой движок использовать (xlrd/openpyxl)
    - Как нормализовать данные
    """

    def __init__(self, config: Dict[str, Any], normalizer: ArticleNormalizer):
        self.config = config
        self.normalizer = normalizer
        self.data_normalizer = DataNormalizer()

    def parse(self, file_path: Path, vendor: str) -> List[PriceItem]:
        """Парсит Excel файл в список PriceItem"""
        try:
            engine = self.config.get('engine', 'openpyxl')
            
            # ========================================
            # СПЕЦИАЛЬНАЯ ЛОГИКА ДЛЯ IEK
            # ========================================
            if vendor == 'IEK':
                df = pd.read_excel(file_path, sheet_name=0, header=None, engine=engine)
                logger.info(f"📊 Загружено {len(df)} строк из {file_path.name}")
                
                # IEK: строка 5 - основные заголовки, строка 6 - подзаголовки
                header_main = df.iloc[5].fillna('').astype(str)
                header_sub = df.iloc[6].fillna('').astype(str)

                # Умное объединение заголовков:
                # - Для колонок с ценой объединяем (чтобы различать "Базовая цена с НДС", "РОЦ с НДС" и т.д.)
                # - Если основной заголовок - email или мусор (@ в названии), используем подзаголовок
                # - Для остальных используем основной заголовок
                combined_headers = []
                for i, (main, sub) in enumerate(zip(header_main, header_sub)):
                    main_clean = main.strip() if main and main not in ['nan', ''] else ''
                    sub_clean = sub.strip() if sub and sub not in ['nan', ''] else ''

                    # Если основной заголовок содержит "цена" - объединяем с подзаголовком
                    if main_clean and 'цена' in main_clean.lower() and sub_clean:
                        combined_headers.append(f"{main_clean} {sub_clean}")
                    # Если основной заголовок содержит @ (email) - используем подзаголовок
                    elif main_clean and '@' in main_clean and sub_clean:
                        combined_headers.append(sub_clean)
                    # Иначе используем основной заголовок (или подзаголовок если основного нет)
                    elif main_clean:
                        combined_headers.append(main_clean)
                    elif sub_clean:
                        combined_headers.append(sub_clean)
                    else:
                        combined_headers.append(f'col_{i}')

                df.columns = combined_headers
                df = df.iloc[7:].reset_index(drop=True)

                logger.info(f"Колонки IEK после объединения (первые 15): {list(df.columns)[:15]}")

                # Ищем колонки содержащие нужные слова для отладки
                article_candidates = [col for col in df.columns if 'артикул' in str(col).lower()]
                name_candidates = [col for col in df.columns if 'наименование' in str(col).lower() or 'название' in str(col).lower() or 'товар' in str(col).lower()]
                price_candidates = [col for col in df.columns if 'цена' in str(col).lower() and 'ндс' in str(col).lower()]

                logger.info(f"IEK - Кандидаты для артикулов: {article_candidates}")
                logger.info(f"IEK - Кандидаты для наименований: {name_candidates}")
                logger.info(f"IEK - Кандидаты для цены: {price_candidates}")
            
            # ========================================
            # DKC
            # ========================================
            elif vendor == 'DKC':
                df = pd.read_excel(file_path, sheet_name=0, header=None, engine=engine)
                logger.info(f"📊 Загружено {len(df)} строк из {file_path.name}")
                
                # Ищем строку с заголовками
                header_row = None
                for idx in range(min(20, len(df))):
                    row_str = df.iloc[idx].astype(str).str.lower().tolist()
                    if any('описание' in cell or 'цена' in cell for cell in row_str):
                        header_row = idx
                        logger.info(f"Найдена строка заголовков DKC: {idx}")
                        break
                
                if header_row is None:
                    logger.warning("Заголовки DKC не найдены, пробуем строку 10")
                    header_row = 10
                
                # Устанавливаем заголовки
                df.columns = df.iloc[header_row]
                df = df.iloc[header_row + 1:].reset_index(drop=True)
                
                # Переименовываем ВСЕ дубликаты колонок
                cols = df.columns.tolist()
                seen = {}
                new_cols = []
                
                for col in cols:
                    if col in seen:
                        seen[col] += 1
                        new_cols.append(f"{col}_{seen[col]}")
                    else:
                        seen[col] = 0
                        new_cols.append(col)
                
                df.columns = new_cols
                logger.info(f"Колонки после удаления дубликатов: {df.columns.tolist()[:10]}")
                
                # Удаляем строки-заголовки групп (где все цены пустые)
                price_cols = [col for col in df.columns if 'цена' in str(col).lower()]
                if price_cols:
                    before = len(df)
                    df = df.dropna(subset=price_cols, how='all')
                    logger.info(f"Удалено {before - len(df)} строк-заголовков групп, осталось {len(df)}")
                
                if len(df) > 0:
                    logger.info(f"Первая строка: Код={df.iloc[0].get('Код', 'N/A')}, Цена={df.iloc[0].get('Цена с НДС, руб./м(шт)', 'N/A')}")
            
            # ========================================
            # ОСТАЛЬНЫЕ (CHINT, SCHNEIDER)
            # ========================================
            else:
                df = pd.read_excel(file_path, sheet_name=0, header=None, engine=engine)
                logger.info(f"📊 Загружено {len(df)} строк из {file_path.name}")
                
                header_row = self._find_header_row(df)
                if header_row is None:
                    raise ValueError("Не найдена строка заголовков")
                
                df.columns = df.iloc[header_row]
                df = df.iloc[header_row + 1:].reset_index(drop=True)

            # ========================================
            # ОБЩАЯ ОБРАБОТКА
            # ========================================
            df = self._map_columns(df)
            df = self._clean_data(df, vendor)
            items = self._to_price_items(df, vendor)
            
            logger.info(f"✅ Успешно распарсено {len(items)} позиций")
            return items

        except Exception as e:
            logger.error(f"Ошибка парсинга {file_path}: {e}", exc_info=True)
            raise

    def _find_header_row(self, df: pd.DataFrame) -> int:
        """Находит строку с заголовками"""
        column_mapping = self.config.get('columns', {})
        required_columns = list(column_mapping.values())

        for idx in range(min(20, len(df))):
            row_values = df.iloc[idx].astype(str).str.lower().tolist()
            matches = sum(
                1 for col in required_columns
                if any(str(col).lower() in val for val in row_values)
            )
            if matches >= 2:  # Минимум 2 совпадения
                return idx

        return 0  # Fallback

    def _map_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Переименовывает колонки согласно маппингу"""
        column_map = self.config.get('column_map', {})
        
        if not column_map:
            return df
        
        # Логируем ДО переименования
        logger.info(f"Колонки ДО маппинга: {df.columns.tolist()}")
        logger.info(f"Маппинг: {column_map}")
        
        # Переименовываем
        df = df.rename(columns=column_map)
        
        # Логируем ПОСЛЕ
        logger.info(f"Колонки ПОСЛЕ маппинга: {df.columns.tolist()}")
        
        return df

    def _clean_data(self, df: pd.DataFrame, vendor: str) -> pd.DataFrame:
        """Очистка данных"""
        logger.info(f"Очистка данных для {vendor}")

        # Удаляем полностью пустые строки
        df = df.dropna(how='all')

        # ДОБАВЬ: Удаляем строки где цена пустая (заголовки групп)
        price_cols = [col for col in df.columns if 'Цена' in str(col) or 'цена' in str(col).lower() or 'price' in str(col).lower()]
        if price_cols:
            df = df.dropna(subset=price_cols, how='all')
            logger.info(f"После удаления строк без цен: {len(df)} записей")

        # Получаем имя колонки артикулов из конфигурации
        columns_config = self.config.get('columns', {})
        article_col = columns_config.get('article', None)

        # Если в конфиге не указано, ищем стандартные варианты
        if article_col is None or article_col not in df.columns:
            for col_name in ['article', 'Код', 'Артикул']:
                if col_name in df.columns:
                    article_col = col_name
                    break

        if article_col is None or article_col not in df.columns:
            logger.error(f"Колонка с артикулами не найдена. Доступные: {df.columns.tolist()}")
            raise ValueError(f"Не найдена колонка с артикулами в данных {vendor}")
        
        # ИСПРАВЬ: Если несколько колонок с одним именем, берем только первую
        if isinstance(df[article_col], pd.DataFrame):
            logger.warning(f"Несколько колонок '{article_col}', используем первую")
            # Переименовываем дубликаты
            cols = df.columns.tolist()
            seen = {}
            new_cols = []
            for col in cols:
                if col in seen:
                    seen[col] += 1
                    new_cols.append(f"{col}_{seen[col]}")
                else:
                    seen[col] = 0
                    new_cols.append(col)
            df.columns = new_cols
            article_col = article_col  # Теперь это единственная колонка с этим именем
        
        # Фильтруем пустые артикулы
        df[article_col] = df[article_col].astype(str).str.strip()
        df = df[df[article_col] != '']
        df = df[df[article_col] != 'nan']
        
        # Убираем дубликаты
        df = df.drop_duplicates(subset=[article_col], keep='first')
        
        logger.info(f"После очистки осталось {len(df)} записей")
        return df

    def _clean_price(self, price_val: Any) -> float:
        """Очищает и конвертирует цену"""
        if pd.isna(price_val):
            return 0.0

        price_str = str(price_val).strip().lower()

        # Проверка на специальные значения
        if any(x in price_str for x in ['запрос', 'договор', 'уточн']):
            return 0.0

        # Очистка от символов
        price_str = re.sub(r'[^\d,.]', '', price_str).replace(',', '.')

        try:
            return float(price_str) if price_str else 0.0
        except ValueError:
            return 0.0

    def _to_price_items(self, df: pd.DataFrame, vendor: str) -> List[PriceItem]:
        """Преобразует DataFrame в список PriceItem"""
        items = []

        # Нормализуем vendor name
        vendor_normalized = self.data_normalizer.normalize_vendor_name(vendor)

        # Получаем маппинг колонок из конфигурации
        columns_config = self.config.get('columns', {})

        # Определяем имена колонок - сначала проверяем конфигурацию, потом стандартные значения
        article_col = columns_config.get('article', 'Код')
        desc_col = columns_config.get('description', 'Описание')
        price_col = columns_config.get('price', 'Цена с НДС, руб./м(шт)')
        unit_col = columns_config.get('units', 'Ед. Изм.')

        logger.info(f"Используем колонки: article={article_col}, price={price_col}")

        # Проверяем что колонки существуют
        if article_col not in df.columns:
            logger.error(f"Колонка '{article_col}' не найдена в {df.columns.tolist()}")
            return []

        if price_col not in df.columns:
            logger.error(f"Колонка '{price_col}' не найдена в {df.columns.tolist()}")
            return []

        # Отладка первых 3 строк
        for idx in range(min(3, len(df))):
            row = df.iloc[idx]
            logger.info(f"DEBUG строка {idx}: article={row[article_col]}, price={row[price_col]}, types: {type(row[article_col])}, {type(row[price_col])}")

        skip_reasons = {'empty_article': 0, 'empty_price': 0, 'price_on_request': 0, 'error': 0}
        error_messages = []

        for idx, row in df.iterrows():
            try:
                # Извлекаем артикул
                article = row[article_col]

                # Проверяем что это не Series
                if isinstance(article, pd.Series):
                    article = article.iloc[0] if len(article) > 0 else None

                # Нормализуем артикул (убираем пробелы, uppercase)
                article_str = self.data_normalizer.normalize_article(str(article) if article else '', vendor_normalized)

                # Проверка пустоты
                if not article_str or article_str in ['NAN', 'NONE']:
                    skip_reasons['empty_article'] += 1
                    continue

                # Извлекаем цену
                price_val = row[price_col]
                if isinstance(price_val, pd.Series):
                    price_val = price_val.iloc[0] if len(price_val) > 0 else None

                # Нормализуем и проверяем цену
                price_cleaned = self.data_normalizer.clean_price_value(price_val)

                # None означает "заказная позиция" - ставим цену 0 для учета в синхронизации
                if price_cleaned is None:
                    price_cleaned = 0.0
                    skip_reasons['price_on_request'] += 1
                    # НЕ пропускаем - создаем PriceItem с ценой 0

                # Если цена пустая (не заказная, а именно пустая) - пропускаем
                if price_cleaned == 0.0 and not self.data_normalizer.is_price_on_request(str(price_val)):
                    skip_reasons['empty_price'] += 1
                    continue

                # Извлекаем описание
                description = ''
                if desc_col in df.columns:
                    desc_val = row[desc_col]
                    if isinstance(desc_val, pd.Series):
                        desc_val = desc_val.iloc[0] if len(desc_val) > 0 else ''
                    description = self.data_normalizer.normalize_description(str(desc_val) if desc_val and not pd.isna(desc_val) else '')

                # Извлекаем и нормализуем единицы измерения
                unit = 'шт'
                if unit_col in df.columns:
                    unit_val = row[unit_col]
                    if isinstance(unit_val, pd.Series):
                        unit_val = unit_val.iloc[0] if len(unit_val) > 0 else 'шт'
                    unit = self.data_normalizer.normalize_unit(str(unit_val) if unit_val and not pd.isna(unit_val) else 'шт')

                # Создаем PriceItem
                item = PriceItem(
                    vendor=vendor_normalized,
                    article=article_str,
                    description=description,
                    price=price_cleaned,
                    units=unit
                )
                items.append(item)

            except Exception as e:  # ← ГЛАВНЫЙ EXCEPT
                skip_reasons['error'] += 1
                if len(error_messages) < 5:  # ← СОХРАНЯЕМ ОШИБКУ
                    error_messages.append(f"idx={idx}, error={type(e).__name__}: {str(e)}")
                continue

        # ← ВЫВОДИМ ОШИБКИ
        if error_messages:
            logger.error(f"Примеры ошибок при создании PriceItem: {error_messages}")

        logger.info(f"Пропущено: пустые артикулы={skip_reasons['empty_article']}, пустые цены={skip_reasons['empty_price']}, заказные={skip_reasons['price_on_request']}, ошибки={skip_reasons['error']}")
        logger.info(f"Преобразовано {len(items)} позиций из {len(df)}")
        return items