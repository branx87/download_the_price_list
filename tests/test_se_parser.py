import sys
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from adapters.parsers.excel_parser import ExcelParser
from utils.normalizer import ArticleNormalizer

config = {
    'engine': 'openpyxl',
    'sheet_name_pattern': 'Тариф Москва',
    'header_row': 2,
    'columns': {
        'article': 'Референс',
        'description': 'Описание референса',
        'price': 'без НДС',
        'units': 'Единица измерения',
        'storage': 'со склада\nМосква',
    }
}

normalizer = ArticleNormalizer()
parser = ExcelParser(config, normalizer)
items = parser.parse(Path('price_files/Tariff АО СЭ 2025 - 06 (от 14.11.2025).xlsm'), 'SE')
print(f'Распарсено: {len(items)} позиций')
for item in items[:5]:
    print(f'  {item.article} | {item.description[:45]} | {item.price} | {item.units} | storage={item.storage!r}')

# Проверяем единицы
units_seen = set(item.units for item in items)
print(f'Уникальные единицы: {units_seen}')
