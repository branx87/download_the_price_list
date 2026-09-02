"""Тесты для FlexiblePriceParser.

Запускать из корня проекта:
    python tests/test_flexible_parser.py

Сначала сгенерирует фикстуры (если их нет), затем прогонит парсер по каждой
и проверит ключевые инварианты: алиасы колонок, опциональные поля, дедуп,
приоритет «для печати» > «наименование».
"""
import sys
from pathlib import Path

# Принудительно UTF-8 для вывода в консоль Windows (cp1251 по умолчанию).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.parsers.flexible_price_parser import FlexiblePriceParser  # noqa: E402
from utils.normalizer import ArticleNormalizer  # noqa: E402

FIXTURES_DIR = PROJECT_ROOT / 'tests' / 'fixtures' / 'flexible'

# Подсветка для вывода в консоль
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'


def _ensure_fixtures() -> None:
    if not (FIXTURES_DIR / 'sample_format1.xlsx').exists():
        print(f'{YELLOW}Фикстуры не найдены — генерируем...{RESET}')
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'generate_samples',
            FIXTURES_DIR / 'generate_samples.py',
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.main()


def _print_items(items, limit: int = 5) -> None:
    for item in items[:limit]:
        print(
            f'  {item.vendor:<6} | {item.article:<22} | '
            f'{item.description[:35]:<35} | {item.price:>8} {item.units}'
        )
    if len(items) > limit:
        print(f'  ... и ещё {len(items) - limit}')


def _check(label: str, condition: bool, detail: str = '') -> bool:
    icon = f'{GREEN}✓{RESET}' if condition else f'{RED}✗{RESET}'
    print(f'  {icon} {label}', end='')
    if detail:
        print(f'  ({detail})', end='')
    print()
    return condition


def test_format1(normalizer: ArticleNormalizer) -> bool:
    """Формат 1: 'Наименование для печати' приоритетнее 'Наименования'."""
    print(f'\n{YELLOW}=== test_format1 ==={RESET}')
    parser = FlexiblePriceParser(
        {
            'vendor_from_column': True,
            'default_vendor': 'GENERIC',
        },
        normalizer,
    )
    items = parser.parse(FIXTURES_DIR / 'sample_format1.xlsx', vendor='GENERIC')

    print(f'Распарсено: {len(items)} позиций')
    _print_items(items)

    ok = True
    ok &= _check('Найдены все 4 строки', len(items) == 4, f'len={len(items)}')
    ok &= _check('Колонка «Цена с НДС» подхвачена', any(float(i.price) > 0 for i in items))
    ok &= _check('Вендор берётся из «Производитель»', all(i.vendor == 'IEK' for i in items))
    ok &= _check(
        'Артикулы нормализованы (UPPERCASE)',
        all(i.article == i.article.upper() for i in items),
    )
    # проверяем, что «по запросу» получило price=0
    por = next((i for i in items if 'DIN' in i.article), None)
    ok &= _check(
        '«по запросу» → price=0',
        por is not None and float(por.price) == 0.0,
        detail=str(por) if por else 'not found',
    )
    # code_1c: колонка «Код» в фикстуре = 00-000001 и т.д. Должна попасть в PriceItem.
    ok &= _check(
        '«Код» (ArticlePC) подхвачен в PriceItem.code_1c',
        all(i.code_1c.startswith('00-') for i in items),
        detail=', '.join(repr(i.code_1c) for i in items[:3]),
    )
    return ok


def test_format2(normalizer: ArticleNormalizer) -> bool:
    """Формат 2: 'Тариф' как цена, 'Ед. изм' как units, лишняя 'Срок' игнорируется."""
    print(f'\n{YELLOW}=== test_format2 ==={RESET}')
    parser = FlexiblePriceParser(
        {
            'vendor_from_column': True,
            'default_vendor': 'GENERIC',
        },
        normalizer,
    )
    items = parser.parse(FIXTURES_DIR / 'sample_format2.xlsx', vendor='GENERIC')

    print(f'Распарсено: {len(items)} позиций')
    _print_items(items)

    ok = True
    ok &= _check('Найдены все 4 строки', len(items) == 4, f'len={len(items)}')
    ok &= _check('Вендор = TDM', all(i.vendor == 'TDM' for i in items))
    ok &= _check('Колонка «Тариф» подхвачена как цена', any(float(i.price) == 410 for i in items))
    units_seen = {i.units for i in items}
    ok &= _check('«упак» нормализована в units', 'упак' in units_seen, detail=str(units_seen))
    # последняя строка «запрос» → price=0
    last = items[-1]
    ok &= _check(
        '«запрос» → price=0',
        float(last.price) == 0.0,
        detail=f'article={last.article}',
    )
    return ok


def test_mixed(normalizer: ArticleNormalizer) -> bool:
    """Книга с двумя листами + служебный лист без обязательных колонок."""
    print(f'\n{YELLOW}=== test_mixed ==={RESET}')
    parser = FlexiblePriceParser(
        {
            'vendor_from_column': True,
            'default_vendor': 'GENERIC',
        },
        normalizer,
    )
    items = parser.parse(FIXTURES_DIR / 'sample_mixed.xlsx', vendor='GENERIC')

    print(f'Распарсено: {len(items)} позиций')
    _print_items(items)

    ok = True
    # Формат1: 2 строки, Формат2: 2 строки, Служебный: пропущен
    ok &= _check('Итого 4 позиции (2+2)', len(items) == 4, f'len={len(items)}')
    vendors = {i.vendor for i in items}
    ok &= _check('Подхвачены оба вендора', vendors == {'IEK', 'TDM'}, detail=str(vendors))
    stats = getattr(parser, '_last_stats', {})
    ok &= _check(
        'Служебный лист пропущен',
        stats.get('with_headers', 0) == 2,
        detail=str(stats),
    )
    return ok


def test_vendor_override(normalizer: ArticleNormalizer) -> bool:
    """Если vendor_from_column=False, вендор берётся из переданного аргумента."""
    print(f'\n{YELLOW}=== test_vendor_override ==={RESET}')
    parser = FlexiblePriceParser(
        {
            'vendor_from_column': False,
            'default_vendor': 'CUSTOM_VENDOR',
        },
        normalizer,
    )
    items = parser.parse(FIXTURES_DIR / 'sample_format2.xlsx', vendor='CUSTOM_VENDOR')

    ok = True
    ok &= _check('Все items имеют CUSTOM_VENDOR', all(i.vendor == 'CUSTOM_VENDOR' for i in items))
    return ok


def test_missing_required(normalizer: ArticleNormalizer) -> bool:
    """Лист без обязательных колонок пропускается без падения."""
    print(f'\n{YELLOW}=== test_missing_required ==={RESET}')
    parser = FlexiblePriceParser(
        {'vendor_from_column': True, 'default_vendor': 'GENERIC'},
        normalizer,
    )
    items = parser.parse(FIXTURES_DIR / 'sample_mixed.xlsx', vendor='GENERIC')

    stats = getattr(parser, '_last_stats', {})
    ok = True
    ok &= _check(
        'with_headers == 2 (служебный отброшен)',
        stats.get('with_headers') == 2,
        detail=str(stats),
    )
    ok &= _check('Не упали, items не пустые', len(items) > 0)
    return ok


def test_real_price_mp(normalizer: ArticleNormalizer) -> bool:
    """Реальный файл «Прайс МП.xlsx» из price_files/ — регрессионный кейс.

    До правки _find_column парсер возвращал 0 items: «Наименование задачи»
    перебивало «Наименование» (description=None в данных), и строки
    отбрасывались. После правки description подхватывает «Наименование»
    (idx 15), price — «Цена с НДС, руб» (idx 21).
    """
    real_file = PROJECT_ROOT / 'price_files' / 'Прайс МП.xlsx'
    if not real_file.exists():
        print(f'\n{YELLOW}=== test_real_price_mp ==={RESET}')
        print(f'  {YELLOW}[SKIP]{RESET} {real_file.name} не найден')
        return True

    print(f'\n{YELLOW}=== test_real_price_mp ==={RESET}')
    parser = FlexiblePriceParser(
        {'vendor_from_column': True, 'default_vendor': 'GENERIC'},
        normalizer,
    )
    items = parser.parse(real_file, vendor='GENERIC')

    print(f'Распарсено: {len(items)} позиций')
    _print_items(items, limit=3)

    stats = getattr(parser, '_last_stats', {})
    ok = True
    ok &= _check(
        'Лист «Расчет» опознан как с заголовками',
        stats.get('with_headers', 0) >= 1,
        detail=str(stats),
    )
    ok &= _check(
        'Позиций > 0 (раньше было 0)',
        len(items) > 0,
        detail=f'len={len(items)}',
    )
    ok &= _check(
        'Вендор подхвачен из «Производитель»',
        all(i.vendor not in ('', 'GENERIC') for i in items),
        detail=', '.join({i.vendor for i in items}),
    )
    ok &= _check(
        'Артикулы непустые',
        all(i.article for i in items),
    )
    ok &= _check(
        'Цена > 0 хотя бы у одной позиции',
        any(float(i.price) > 0 for i in items),
    )
    return ok


def test_column_priority(normalizer: ArticleNormalizer) -> bool:
    """«Код» и «Артикул РС» уходят в code_1c, а не в article.

    Без приоритизации ролей «Артикул РС» подпал бы под article (содержит
    «артикул»), что неверно — это код 1С (ArticlePC).
    """
    print(f'\n{YELLOW}=== test_column_priority ==={RESET}')
    parser = FlexiblePriceParser(
        {'vendor_from_column': True, 'default_vendor': 'GENERIC'},
        normalizer,
    )

    # Случай 1: «Код» в одной колонке с «Артикул»
    headers = ['Производитель', 'Артикул', 'Код', 'Наименование', 'Тариф']
    col_map = parser.map_columns(headers)
    print(f'  headers={headers}')
    print(f'  col_map={col_map}')
    ok = True
    ok &= _check(
        '«Артикул» → article',
        col_map.get('article') == 1,
        detail=f'article={col_map.get("article")}',
    )
    ok &= _check(
        '«Код» → code_1c (а не article)',
        col_map.get('code_1c') == 2,
        detail=f'code_1c={col_map.get("code_1c")}',
    )
    ok &= _check(
        '«Тариф» → price',
        col_map.get('price') == 4,
        detail=f'price={col_map.get("price")}',
    )

    # Случай 2: «Артикул РС» и «Артикул» рядом — РС уходит в code_1c
    headers2 = ['Производитель', 'Артикул', 'Артикул РС', 'Наименование', 'Тариф']
    col_map2 = parser.map_columns(headers2)
    print(f'\n  headers={headers2}')
    print(f'  col_map={col_map2}')
    ok &= _check(
        '«Артикул РС» → code_1c',
        col_map2.get('code_1c') == 2,
        detail=f'code_1c={col_map2.get("code_1c")}',
    )
    ok &= _check(
        '«Артикул» (без РС) → article',
        col_map2.get('article') == 1,
        detail=f'article={col_map2.get("article")}',
    )

    # Случай 3: реальный кейс «Прайс МП.xlsx» — длинные составные заголовки.
    # «Наименование задачи» и «Наименование» рядом → description должна быть
    # «Наименование» (idx 15), а не «Наименование задачи» (idx 2).
    # «Тариф» и «Цена с НДС, руб» рядом → price должна быть
    # «Цена с НДС, руб» (idx 21), а не «Тариф» (idx 18).
    headers3 = [
        '№ п.п.', 'Номер процесса', 'Наименование задачи',
        'Диспетчерское наименование', 'Тип НКУ', 'Степень секционирования',
        'Кол-во вводов', 'Номинальный ток вводного аппарата',
        'Кол-во отходящих фидеров', 'Высота', 'Ширина', 'Глубина', 'IP',
        'Производитель',  # 13
        'Артикул',        # 14
        'Наименование',   # 15
        'Кол-во',         # 16
        'Ед. изм',        # 17
        'Тариф',          # 18
        'Валюта',         # 19
        'Скидка, %',      # 20
        'Цена с НДС, руб',  # 21
        'Стоимость с НДС, руб',  # 22
        'Срок поставки',
        'Трудозатраты, чел*час',
        'Артикул РС',     # 25
    ]
    col_map3 = parser.map_columns(headers3)
    print(f'\n  headers={headers3}')
    print(f'  col_map={col_map3}')
    ok &= _check(
        '«Наименование» (а не «Наименование задачи») → description',
        col_map3.get('description') == 15,
        detail=f'description={col_map3.get("description")}',
    )
    ok &= _check(
        '«Цена с НДС, руб» (а не «Тариф») → price',
        col_map3.get('price') == 21,
        detail=f'price={col_map3.get("price")}',
    )
    ok &= _check(
        '«Артикул РС» → code_1c',
        col_map3.get('code_1c') == 25,
        detail=f'code_1c={col_map3.get("code_1c")}',
    )
    ok &= _check(
        '«Прочие ПР» (любая manufacturer) → manufacturer',
        col_map3.get('manufacturer') == 13,
        detail=f'manufacturer={col_map3.get("manufacturer")}',
    )

    return ok


def main() -> int:
    _ensure_fixtures()

    normalizer = ArticleNormalizer()
    results = [
        test_format1(normalizer),
        test_format2(normalizer),
        test_mixed(normalizer),
        test_vendor_override(normalizer),
        test_missing_required(normalizer),
        test_real_price_mp(normalizer),
        test_column_priority(normalizer),
    ]

    print(f'\n{YELLOW}{"=" * 50}{RESET}')
    passed = sum(results)
    total = len(results)
    color = GREEN if passed == total else RED
    print(f'{color}Результат: {passed}/{total} тестов прошло{RESET}')
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
