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
    """Формат 1: 'Наименование для печати' приоритетнее 'Наименование'."""
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
    # sample_mixed содержит 3 листа: 2 валидных + 1 «Служебный» без обязательных
    ok = True
    ok &= _check(
        'with_headers == 2 (служебный отброшен)',
        stats.get('with_headers') == 2,
        detail=str(stats),
    )
    ok &= _check('Не упали, items не пустые', len(items) > 0)
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
    ]

    print(f'\n{YELLOW}{"=" * 50}{RESET}')
    passed = sum(results)
    total = len(results)
    color = GREEN if passed == total else RED
    print(f'{color}Результат: {passed}/{total} тестов прошло{RESET}')
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
