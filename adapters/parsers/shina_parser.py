import logging
import pandas as pd
from pathlib import Path

logger = logging.getLogger(__name__)


class ShinaParser:
    """Парсер Excel-файла шин (вендор ШИНА).

    Ожидаемый формат файла:
    - Строка 1 (row 0): заголовки колонок + ячейки цены за кг (сразу после метки материала)
      Примеры меток: «Цена за 1кг МЕДЬ» → следующая ячейка = числовое значение
    - Строки 2+ (row 1+): данные (Part_Num, Descr, Вес 1м)
    """

    def parse(self, file_path: str | Path) -> dict:
        """Возвращает dict:
        {
            'items': [{'part_num', 'descr', 'weight_per_m', 'material'}],
            'copper_price_per_kg': float,
            'alum_price_per_kg': float,
        }
        """
        df = pd.read_excel(str(file_path), header=None, engine='openpyxl')
        if df.empty:
            raise ValueError("Excel-файл пуст")

        header_row = df.iloc[0]

        col_part_num = self._find_col(header_row, 'part_num')
        col_descr    = self._find_col(header_row, 'descr')
        col_weight   = self._find_col(header_row, 'вес')
        col_copper   = self._find_price_col(header_row, 'медь')
        col_alum     = self._find_price_col(header_row, 'алюм')

        missing = {
            'Part_Num': col_part_num,
            'Descr':    col_descr,
            'Вес':      col_weight,
            'Медь':     col_copper,
            'Алюм':     col_alum,
        }
        not_found = [k for k, v in missing.items() if v is None]
        if not_found:
            raise ValueError(f"Не найдены колонки: {', '.join(not_found)}")

        copper_price = float(header_row.iloc[col_copper])
        alum_price   = float(header_row.iloc[col_alum])

        items = []
        skipped_no_part = 0
        skipped_no_weight = 0
        skipped_bad_weight = 0
        for idx in range(1, len(df)):
            row        = df.iloc[idx]
            part_num   = self._safe_str(row.iloc[col_part_num])
            descr      = self._safe_str(row.iloc[col_descr])
            weight_raw = row.iloc[col_weight]

            if not part_num:
                skipped_no_part += 1
                continue

            if not pd.notna(weight_raw):
                skipped_no_weight += 1
                continue

            # Поддержка строк с запятой как разделителем (русская локаль Excel)
            try:
                weight = float(str(weight_raw).replace(',', '.'))
            except (ValueError, TypeError):
                skipped_bad_weight += 1
                logger.debug("[SHINA] Пропущена строка %d: не удалось разобрать вес %r (part_num=%r)",
                             idx + 1, weight_raw, part_num)
                continue

            material = 'алюм' if 'алюминиев' in descr.lower() else 'медь'
            items.append({
                'part_num':     part_num,
                'descr':        descr,
                'weight_per_m': weight,
                'material':     material,
            })

        logger.info(
            "[SHINA] Парсер: медь=%.2f, алюм=%.2f, позиций=%d "
            "(пропущено: нет_артикула=%d, нет_веса=%d, плохой_вес=%d)",
            copper_price, alum_price, len(items),
            skipped_no_part, skipped_no_weight, skipped_bad_weight
        )
        return {
            'items':               items,
            'copper_price_per_kg': copper_price,
            'alum_price_per_kg':   alum_price,
        }

    def _find_col(self, header_row: pd.Series, keyword: str) -> int | None:
        keyword = keyword.lower()
        for i, val in enumerate(header_row):
            if isinstance(val, str) and keyword in val.lower():
                return i
        return None

    def _find_price_col(self, header_row: pd.Series, material_keyword: str) -> int | None:
        """Ищет колонку ПОСЛЕ метки материала, содержащую числовое значение."""
        label_col = self._find_col(header_row, material_keyword)
        if label_col is None:
            return None
        next_col = label_col + 1
        if next_col < len(header_row):
            val = header_row.iloc[next_col]
            if isinstance(val, (int, float)) and not pd.isna(val):
                return next_col
        return None

    @staticmethod
    def _safe_str(val) -> str:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return ''
        return str(val).strip()
