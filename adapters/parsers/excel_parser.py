import logging
import pandas as pd
from pathlib import Path
from decimal import Decimal
from typing import List, Dict, Any, Optional
import re

from domain.interfaces.parser import IParser
from domain.entities.price_item import PriceItem
from utils.normalizer import ArticleNormalizer
from domain.services.data_normalizer import DataNormalizer


logger = logging.getLogger(__name__)


class ExcelParser(IParser):
    """
    Универсальный парсер Excel файлов с конфигурацией под вендора.

    Поддерживаемые параметры parser_config:
      engine            — 'openpyxl' или 'xlrd'
      sheet_name_pattern— подстрока в имени листа (иначе берётся первый лист)
      header_row        — явный 0-based индекс строки заголовков
      header_rows_combine — [row1, row2] — объединить две строки в заголовок (IEK-стиль)
      data_start_row    — 0-based индекс первой строки данных (после header_rows_combine)
      columns           — маппинг {article, description, price, units, storage} → название колонки
    """

    def __init__(self, config: Dict[str, Any], normalizer: ArticleNormalizer):
        self.config = config
        self.normalizer = normalizer
        self.data_normalizer = DataNormalizer()

    def parse(self, file_path: Path, vendor: str) -> List[PriceItem]:
        try:
            engine = self.config.get('engine', 'openpyxl')
            sheet = self._resolve_sheet(file_path, engine)
            df = self._load_dataframe(file_path, engine, sheet)
            df = self._map_columns(df)
            df = self._clean_data(df, vendor)
            items = self._to_price_items(df, vendor)
            logger.info(f"✅ Успешно распарсено {len(items)} позиций")
            return items
        except Exception as e:
            logger.error(f"Ошибка парсинга {file_path}: {e}", exc_info=True)
            raise

    def _resolve_sheet(self, file_path: Path, engine: str):
        """Возвращает имя листа по паттерну или 0 (первый лист)."""
        pattern = self.config.get('sheet_name_pattern')
        if not pattern:
            return 0
        xls = pd.ExcelFile(file_path, engine=engine)
        for name in xls.sheet_names:
            if pattern.lower() in name.lower():
                logger.info(f"Найден лист по паттерну '{pattern}': {name!r}")
                return name
        raise ValueError(f"Лист с паттерном '{pattern}' не найден в {file_path.name}")

    def _load_dataframe(self, file_path: Path, engine: str, sheet) -> pd.DataFrame:
        """Загружает DataFrame с правильными заголовками согласно конфигу."""
        df = pd.read_excel(file_path, sheet_name=sheet, header=None, engine=engine)
        logger.info(f"📊 Загружено {len(df)} строк из {Path(file_path).name}")

        if 'header_rows_combine' in self.config:
            df = self._apply_combined_headers(df)
        elif 'header_row' in self.config:
            hr = self.config['header_row']
            df.columns = df.iloc[hr]
            df = df.iloc[hr + 1:].reset_index(drop=True)
        else:
            hr = self._find_header_row(df)
            df.columns = df.iloc[hr]
            df = df.iloc[hr + 1:].reset_index(drop=True)

        df = self._rename_duplicate_columns(df)
        return df

    def _apply_combined_headers(self, df: pd.DataFrame) -> pd.DataFrame:
        """Объединяет несколько строк заголовков в одну (для IEK-стиля)."""
        rows_idx = self.config['header_rows_combine']
        data_start = self.config.get('data_start_row', rows_idx[-1] + 1)

        main_row = df.iloc[rows_idx[0]].fillna('').astype(str)
        sub_row = df.iloc[rows_idx[1]].fillna('').astype(str) if len(rows_idx) > 1 else None

        combined = []
        for i, main in enumerate(main_row):
            main_clean = main.strip() if main not in ['nan', ''] else ''
            sub_clean = (sub_row.iloc[i].strip()
                         if sub_row is not None and sub_row.iloc[i] not in ['nan', '']
                         else '')

            if main_clean and 'цена' in main_clean.lower() and sub_clean:
                combined.append(f"{main_clean} {sub_clean}")
            elif main_clean and '@' in main_clean and sub_clean:
                combined.append(sub_clean)
            elif main_clean:
                combined.append(main_clean)
            elif sub_clean:
                combined.append(sub_clean)
            else:
                combined.append(f'col_{i}')

        df.columns = combined
        df = df.iloc[data_start:].reset_index(drop=True)
        logger.info(f"Колонки после объединения (первые 15): {list(df.columns)[:15]}")
        return df

    def _rename_duplicate_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Переименовывает дублирующиеся колонки, добавляя суффикс _N."""
        seen: Dict[str, int] = {}
        new_cols = []
        for col in df.columns:
            col_str = str(col)
            if col_str in seen:
                seen[col_str] += 1
                new_cols.append(f"{col_str}_{seen[col_str]}")
            else:
                seen[col_str] = 0
                new_cols.append(col_str)
        df.columns = new_cols
        return df

    def _find_header_row(self, df: pd.DataFrame) -> int:
        """Находит строку с заголовками по именам из конфига (min 2 совпадения)."""
        required_columns = list(self.config.get('columns', {}).values())
        for idx in range(min(20, len(df))):
            row_values = [str(v).lower() for v in df.iloc[idx].tolist()]
            matches = sum(
                1 for col in required_columns
                if any(str(col).lower() in val for val in row_values)
            )
            if matches >= 2:
                return idx
        return 0

    def _map_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Переименовывает колонки согласно column_map (если задан)."""
        column_map = self.config.get('column_map', {})
        if not column_map:
            return df
        logger.info(f"Применяем column_map: {column_map}")
        return df.rename(columns=column_map)

    def _clean_data(self, df: pd.DataFrame, vendor: str) -> pd.DataFrame:
        """Очистка данных: убирает пустые строки, строки без цены и артикула."""
        logger.info(f"Очистка данных для {vendor}")
        df = df.dropna(how='all')

        price_cols = [col for col in df.columns if 'цена' in str(col).lower() or 'price' in str(col).lower()]
        if price_cols:
            df = df.dropna(subset=price_cols, how='all')
            logger.info(f"После удаления строк без цен: {len(df)} записей")

        columns_config = self.config.get('columns', {})
        article_col = columns_config.get('article')

        if article_col is None or article_col not in df.columns:
            for col_name in ['article', 'Код', 'Артикул']:
                if col_name in df.columns:
                    article_col = col_name
                    break

        if article_col is None or article_col not in df.columns:
            logger.error(f"Колонка с артикулами не найдена. Доступные: {df.columns.tolist()}")
            raise ValueError(f"Не найдена колонка с артикулами в данных {vendor}")

        df[article_col] = df[article_col].astype(str).str.strip()
        df = df[df[article_col] != '']
        df = df[df[article_col] != 'nan']
        df = df.drop_duplicates(subset=[article_col], keep='first')

        logger.info(f"После очистки осталось {len(df)} записей")
        return df

    def _clean_price(self, price_val: Any) -> float:
        if pd.isna(price_val):
            return 0.0
        price_str = str(price_val).strip().lower()
        if any(x in price_str for x in ['запрос', 'договор', 'уточн']):
            return 0.0
        price_str = re.sub(r'[^\d,.]', '', price_str).replace(',', '.')
        try:
            return float(price_str) if price_str else 0.0
        except ValueError:
            return 0.0

    def _find_col(self, df: pd.DataFrame, name: str) -> str:
        """Точное совпадение, затем поиск по подстроке (без учёта регистра)."""
        if name in df.columns:
            return name
        name_lower = name.lower()
        for c in df.columns:
            if name_lower in str(c).lower():
                return c
        return name  # вернём как есть — ошибка будет залогирована ниже

    def _to_price_items(self, df: pd.DataFrame, vendor: str) -> List[PriceItem]:
        """Преобразует DataFrame в список PriceItem."""
        items = []
        vendor_normalized = self.data_normalizer.normalize_vendor_name(vendor)
        columns_config = self.config.get('columns', {})

        article_col = self._find_col(df, columns_config.get('article', 'Код'))
        desc_col = self._find_col(df, columns_config.get('description', 'Описание'))
        price_col = self._find_col(df, columns_config.get('price', 'Цена с НДС, руб./м(шт)'))
        unit_col = self._find_col(df, columns_config.get('units', 'Ед. Изм.'))
        storage_col = self._find_col(df, columns_config['storage']) if 'storage' in columns_config else None

        logger.info(f"Используем колонки: article={article_col}, price={price_col}")

        if article_col not in df.columns:
            logger.error(f"Колонка '{article_col}' не найдена в {df.columns.tolist()}")
            return []

        if price_col not in df.columns:
            logger.error(f"Колонка '{price_col}' не найдена в {df.columns.tolist()}")
            return []

        skip_reasons = {'empty_article': 0, 'empty_price': 0, 'price_on_request': 0, 'error': 0}
        error_messages = []

        for idx, row in df.iterrows():
            try:
                article = row[article_col]
                if isinstance(article, pd.Series):
                    article = article.iloc[0] if len(article) > 0 else None

                article_str = self.data_normalizer.normalize_article(
                    str(article) if article else '', vendor_normalized
                )
                if not article_str or article_str in ['NAN', 'NONE']:
                    skip_reasons['empty_article'] += 1
                    continue

                price_val = row[price_col]
                if isinstance(price_val, pd.Series):
                    price_val = price_val.iloc[0] if len(price_val) > 0 else None

                price_cleaned = self.data_normalizer.clean_price_value(price_val)
                if price_cleaned is None:
                    price_cleaned = 0.0
                    skip_reasons['price_on_request'] += 1

                if price_cleaned == 0.0 and not self.data_normalizer.is_price_on_request(str(price_val)):
                    skip_reasons['empty_price'] += 1
                    continue

                description = ''
                if desc_col in df.columns:
                    desc_val = row[desc_col]
                    if isinstance(desc_val, pd.Series):
                        desc_val = desc_val.iloc[0] if len(desc_val) > 0 else ''
                    description = self.data_normalizer.normalize_description(
                        str(desc_val) if desc_val and not pd.isna(desc_val) else ''
                    )

                if self.config.get('concat_article_to_description') and description and article_str:
                    description = f"{description} ({article_str})"

                unit = 'шт'
                if unit_col in df.columns:
                    unit_val = row[unit_col]
                    if isinstance(unit_val, pd.Series):
                        unit_val = unit_val.iloc[0] if len(unit_val) > 0 else 'шт'
                    unit = self.data_normalizer.normalize_unit(
                        str(unit_val) if unit_val and not pd.isna(unit_val) else 'шт'
                    )

                storage = ''
                if storage_col and storage_col in df.columns:
                    sv = row[storage_col]
                    if isinstance(sv, pd.Series):
                        sv = sv.iloc[0] if len(sv) > 0 else ''
                    storage = str(sv).strip() if sv is not None and not pd.isna(sv) else ''

                items.append(PriceItem(
                    vendor=vendor_normalized,
                    article=article_str,
                    description=description,
                    price=price_cleaned,
                    units=unit,
                    storage=storage,
                ))

            except Exception as e:
                skip_reasons['error'] += 1
                if len(error_messages) < 5:
                    error_messages.append(f"idx={idx}, error={type(e).__name__}: {str(e)}")
                continue

        if error_messages:
            logger.error(f"Примеры ошибок при создании PriceItem: {error_messages}")

        logger.info(
            f"Пропущено: пустые артикулы={skip_reasons['empty_article']}, "
            f"пустые цены={skip_reasons['empty_price']}, заказные={skip_reasons['price_on_request']}, "
            f"ошибки={skip_reasons['error']}"
        )
        logger.info(f"Преобразовано {len(items)} позиций из {len(df)}")
        return items
