import logging
import math
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# Алиасы колонок маппинга. Порядок = приоритет (ранний keyword выигрывает).
# Все сравнения — case-insensitive substring (substring match).
_MAPPING_ALIASES: Dict[str, List[str]] = {
    # description — приоритет по запросу пользователя: «Наименование для печати» обычно полнее
    'description': [
        'наименование для печати',
        'наименование',
        'полное наименование',
        'описание',
    ],
    # article — голое «Артикул». «Артикул РС» живёт в code_1c (см. порядок ниже).
    # 'арт.' (с точкой) — специфичный алиас для короткого написания.
    'article': [
        'арт.',
        'артикул',
    ],
    # code_1c — код 1С (ArticlePC).
    'code_1c': [
        'артикул рс',
        'код 1с',
        'код1с',
        'код',
    ],
    'manufacturer': [
        'производитель',
        'бренд',
        'вендор',
        'марка',
    ],
    'units': [
        'единица хранения',
        'единица измерения',
        'ед. изм',
        'ед изм',
        'ед.',
    ],
}

# Обязательные роли: если их нет в заголовках, файл НЕ считается маппингом.
_MAPPING_REQUIRED = {'article', 'code_1c', 'manufacturer'}


def _normalize_header(value) -> str:
    """Приводит заголовок к виду для сравнения алиасов: lowercase + strip."""
    if value is None:
        return ''
    return str(value).strip().lower()


def _find_column(
    headers: List[str],
    keywords: List[str],
    used: Optional[Set[int]] = None,
) -> Optional[int]:
    """Возвращает индекс лучшей колонки, заголовок которой содержит keyword (substring).

    Лучшая = (1) самый приоритетный keyword (раннее вхождение в списке),
            (2) при равном — самый короткий заголовок (точное совпадение).
    Параметр used — множество уже занятых индексов.
    """
    skipped = used or set()
    best_idx: Optional[int] = None
    best_prio: int = len(keywords) + 1
    best_len: int = 10 ** 9

    for idx, header in enumerate(headers):
        if idx in skipped:
            continue
        h = _normalize_header(header)
        if not h:
            continue
        for prio, kw in enumerate(keywords):
            if kw in h:
                if prio < best_prio or (prio == best_prio and len(h) < best_len):
                    best_idx = idx
                    best_prio = prio
                    best_len = len(h)
                break
    return best_idx


def _map_mapping_columns(headers: List[str]) -> Optional[Dict[str, int]]:
    """Ищет колонки маппинга по алиасам. Возвращает dict или None если не все required найдены.

    Порядок ролей важен: code_1c раньше article, чтобы «Артикул РС» ушёл в code_1c,
    а не в article.
    """
    used: Set[int] = set()
    col_map: Dict[str, int] = {}

    for role in ('code_1c', 'description', 'article', 'manufacturer', 'units'):
        idx = _find_column(headers, _MAPPING_ALIASES[role], used)
        if idx is not None:
            col_map[role] = idx
            used.add(idx)

    missing = _MAPPING_REQUIRED - set(col_map.keys())
    if missing:
        return None
    return col_map


class MappingParser:
    """Парсер Excel-файла (.xls/.xlsx) с маппингом артикул+производитель → код 1С.

    Колонки определяются по алиасам, не по точному имени файла.
    Обязательные роли: article, manufacturer, code_1c.
    Опциональные: description (приоритет «Наименование для печати»), units.

    Возвращает список:
        [{article, manufacturer, code, description, units}, ...]

    article      — артикул (как в файле, strip)
    manufacturer — производитель (UPPERCASE)
    code         — код 1С (ArticlePC)
    description  — наименование (из «Наименование для печати» если есть), иначе ''
    units        — единица измерения, иначе ''
    """

    def parse(self, file_path: str | Path) -> List[dict]:
        file_path_str = str(file_path)
        ext = Path(file_path_str).suffix.lower()

        rows = self._read_rows(file_path_str, ext)
        if len(rows) < 2:
            raise ValueError("Excel-файл пуст или содержит только заголовок")

        header_idx, header_row = self._find_header_row(rows)
        if header_idx is None:
            raise ValueError(
                "Не найдены обязательные колонки маппинга: "
                "Артикул + Производитель + Код (или их алиасы: "
                "«Артикул», «Производитель/Бренд», «Код/Артикул РС/Код 1С»). "
                "Проверьте, что файл содержит нужные столбцы."
            )

        col_map = _map_mapping_columns(header_row)
        if col_map is None:
            missing = _MAPPING_REQUIRED - set(col_map.keys()) if col_map else _MAPPING_REQUIRED
            actual = ', '.join(h for h in header_row if h)
            raise ValueError(
                f"Не найдены обязательные колонки: {', '.join(sorted(missing))}. "
                f"Фактические колонки: {actual}"
            )

        data_rows = rows[header_idx + 1:]
        items: List[dict] = []
        skipped_empty = 0
        for row in data_rows:
            article = self._safe_str(self._get(row, col_map['article']))
            manufacturer = self._safe_str(self._get(row, col_map['manufacturer'])).upper()
            code = self._safe_str(self._get(row, col_map['code_1c']))
            if not (article and manufacturer and code):
                skipped_empty += 1
                continue
            description = ''
            if 'description' in col_map:
                description = self._safe_str(self._get(row, col_map['description']))
            units = ''
            if 'units' in col_map:
                units = self._safe_str(self._get(row, col_map['units']))
            items.append({
                'article': article,
                'manufacturer': manufacturer,
                'code': code,
                'description': description,
                'units': units,
            })

        logger.info(
            "[MAPPING] Парсер: позиций=%d, пропущено=%d (пустые поля), "
            "description=%s, units=%s",
            len(items), skipped_empty,
            'description' in col_map, 'units' in col_map,
        )
        return items

    def detect(self, file_path: str | Path) -> bool:
        """Проверяет, является ли файл маппингом 1С по заголовкам колонок.

        Используется в upload_price_handler для определения типа файла
        без зависимости от имени. Возвращает True если найдены все required роли.
        """
        try:
            ext = Path(str(file_path)).suffix.lower()
            rows = self._read_rows(str(file_path), ext)
        except Exception as e:
            logger.warning("[MAPPING] detect: не удалось прочитать файл: %s", e)
            return False
        if not rows:
            return False
        header_idx, header_row = self._find_header_row(rows)
        if header_idx is None:
            return False
        col_map = _map_mapping_columns(header_row)
        return col_map is not None

    # ── Внутренние хелперы ───────────────────────────────────────────────────

    @staticmethod
    def _read_rows(file_path: str, ext: str) -> List[List]:
        """Читает файл и возвращает список строк (list of list)."""
        if ext == '.xls':
            try:
                import xlrd
            except ImportError:
                raise ValueError("Для чтения .xls нужна библиотека xlrd: pip install xlrd")
            book = xlrd.open_workbook(file_path, formatting_info=False)
            sheet = book.sheet_by_index(0)
            return [
                [sheet.cell_value(r, c) for c in range(sheet.ncols)]
                for r in range(sheet.nrows)
            ]
        if ext == '.xlsx':
            try:
                import openpyxl
            except ImportError:
                raise ValueError("Для чтения .xlsx нужна библиотека openpyxl: pip install openpyxl")
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            ws = wb.active
            rows = [list(row) for row in ws.iter_rows(values_only=True)]
            wb.close()
            return rows
        raise ValueError(f"Неподдерживаемое расширение файла: {ext}. Ожидается .xls или .xlsx")

    @staticmethod
    def _find_header_row(rows: List[List]) -> Tuple[Optional[int], List[str]]:
        """Ищет строку заголовков среди первых 10 строк файла.

        Критерий: на строке должны быть найдены все required-роли маппинга
        (article + code_1c + manufacturer).
        """
        limit = min(10, len(rows))
        for idx in range(limit):
            headers = [_normalize_header(c) for c in rows[idx]]
            col_map = _map_mapping_columns(headers)
            if col_map is not None:
                return idx, headers
        return None, []

    @staticmethod
    def _get(row: List, idx: int):
        """Безопасный доступ к колонке — если в строке меньше колонок, вернёт None."""
        return row[idx] if idx < len(row) else None

    @staticmethod
    def _safe_str(val) -> str:
        if val is None:
            return ''
        if isinstance(val, float):
            try:
                if math.isnan(val):
                    return ''
            except (TypeError, ValueError):
                pass
        return str(val).strip()