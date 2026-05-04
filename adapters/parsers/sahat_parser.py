import logging
import pandas as pd
from pathlib import Path

logger = logging.getLogger(__name__)

_MCCB_COLS = {
    'part_num':      15,
    'base_price':    16,
    'price_z':       19,
    'price_p':       20,
    'price_release': 21,
    'price_aux':     22,
    'price_alm':     23,
    'price_shunt':   24,
    'price_uv':      25,
    'price_h':       26,
    'price_r':       27,
    'price_c':       28,
}

_ACB_COLS = {
    'part_num':        15,
    'base_price':      16,
    'price_basic':     18,
    'price_with_ctrl': 19,
    'price_sw3_a4':    20,
    'price_sw3_hp':    21,
    'price_sw3_hq':    22,
    'price_sw3_hg':    23,
    'price_horiz_2s':  24,
    'price_horiz_3s':  25,
    'price_vert_2l':   26,
    'price_vert_3l':   27,
    'price_lock1':     28,
    'price_lock2':     29,
    'price_lock3':     30,
    'price_lock5':     31,
    'price_modbus':    32,
}


class SahatParser:
    """Парсер Excel-прайса Sahat DDP (листы MCCB и ACB)."""

    def parse(self, file_path: str | Path) -> dict:
        """Возвращает {'mccb': [...], 'acb': [...]}."""
        return {
            'mccb': self._parse_sheet(file_path, 'MCCB', _MCCB_COLS),
            'acb':  self._parse_sheet(file_path, 'ACB',  _ACB_COLS),
        }

    def _parse_sheet(self, file_path, sheet_name: str, col_map: dict) -> list:
        df = pd.read_excel(str(file_path), sheet_name=sheet_name, header=None, engine='openpyxl')
        items = []
        skipped = 0
        # header row = 4 (0-based), data starts at row 5
        for idx in range(5, len(df)):
            row = df.iloc[idx]
            part_num = self._safe_str(row.iloc[col_map['part_num']])
            if not part_num:
                skipped += 1
                continue
            item = {'part_num': part_num}
            for key, col_idx in col_map.items():
                if key == 'part_num':
                    continue
                val = row.iloc[col_idx] if col_idx < len(row) else None
                item[key] = self._safe_float(val)
            items.append(item)
        logger.info("[SELECTRIC] %s: распарсено=%d, пропущено=%d", sheet_name, len(items), skipped)
        return items

    @staticmethod
    def _safe_str(val) -> str:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return ''
        return str(val).strip()

    @staticmethod
    def _safe_float(val) -> float:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0
