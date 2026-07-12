import logging
import pandas as pd
from pathlib import Path

logger = logging.getLogger(__name__)


class MappingParser:
    """Парсер Excel-файла с маппингом артикул+производитель -> код 1С.

    Ожидаемый формат файла:
    - Колонки: Наименование, Артикул, Код, Единица хранения, Вид номенклатуры, Производитель
    - Формат: .xls (xlrd движок)
    """

    def parse(self, file_path: str | Path) -> list[dict]:
        """Возвращает список [{article, manufacturer, code}].

        article      — артикул (нормализованный)
        manufacturer — производитель (нормализованный uppercase)
        code         — код 1С (ArticlePC)
        """
        df = pd.read_excel(str(file_path), header=0, engine='xlrd')

        if df.empty:
            raise ValueError("Excel-файл пуст")

        required_cols = {'Артикул', 'Код', 'Производитель'}
        actual_cols = set(df.columns.str.strip())
        missing = required_cols - actual_cols
        if missing:
            raise ValueError(
                f"Не найдены колонки: {', '.join(missing)}. "
                f"Фактические колонки: {', '.join(actual_cols)}"
            )

        # Vectorized операции вместо iterrows()
        articles = df['Артикул'].apply(self._safe_str)
        manufacturers = df['Производитель'].apply(self._safe_str)
        codes = df['Код'].apply(self._safe_str)

        # Фильтруем пустые строки
        mask = articles.astype(bool) & manufacturers.astype(bool) & codes.astype(bool)
        skipped = int((~mask).sum())

        items = [
            {'article': a, 'manufacturer': m.upper(), 'code': c}
            for a, m, c in zip(articles[mask], manufacturers[mask], codes[mask])
        ]

        logger.info(
            "[MAPPING] Парсер: позиций=%d, пропущено=%d (пустые поля)",
            len(items), skipped,
        )
        return items

    @staticmethod
    def _safe_str(val) -> str:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return ''
        return str(val).strip()
