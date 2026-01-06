"""
Сервис нормализации данных для обеспечения консистентности при парсинге и сравнении.

Принципы:
- Единая точка входа для всех нормализаций
- KISS: простые правила без излишней сложности
- OOP: инкапсуляция логики нормализации
"""

import re
from typing import Optional


class DataNormalizer:
    """
    Централизованный сервис нормализации данных.

    Обеспечивает консистентность:
    - Артикулов (trim, uppercase, убираем лишние пробелы)
    - Единиц измерения (стандартизация)
    - Vendor names (маппинг разных написаний)
    """

    # Таблица соответствий единиц измерения
    UNIT_MAPPING = {
        # Штуки
        'шт': 'шт',
        'шт.': 'шт',
        'штука': 'шт',
        'штук': 'шт',
        'piece': 'шт',
        'pcs': 'шт',

        # Комплекты
        'компл': 'компл',
        'компл.': 'компл',
        'комплект': 'компл',
        'set': 'компл',

        # Метры
        'м': 'м',
        'м.': 'м',
        'метр': 'м',
        'метров': 'м',
        'meter': 'м',

        # Упаковки
        'уп': 'уп',
        'уп.': 'уп',
        'упаковка': 'уп',
        'pack': 'уп',

        # Килограммы
        'кг': 'кг',
        'кг.': 'кг',
        'килограмм': 'кг',
        'kg': 'кг',

        # Литры
        'л': 'л',
        'л.': 'л',
        'литр': 'л',
        'liter': 'л',
    }

    # Таблица соответствий vendor names
    VENDOR_NAME_MAPPING = {
        'owen': 'OWEN',
        'овен': 'OWEN',
        'оуэн': 'OWEN',
    }

    @staticmethod
    def normalize_article(article: str, vendor: Optional[str] = None) -> str:
        """
        Нормализует артикул.

        Правила:
        1. Убираем пробелы в начале и конце
        2. Убираем ВСЕ пробелы внутри (для консистентности с парсерами)
        3. Приводим к верхнему регистру
        4. СОХРАНЯЕМ ведущие нули (критично для некоторых артикулов!)

        Args:
            article: Исходный артикул
            vendor: Название вендора (для специфичных правил)

        Returns:
            Нормализованный артикул
        """
        if not article:
            return ""

        # Приводим к строке (БЕЗ преобразования в число - чтобы сохранить ведущие нули!)
        article = str(article).strip()

        # Убираем ВСЕ пробелы (согласно ArticleNormalizer)
        article = re.sub(r'\s+', '', article)

        # Uppercase для консистентности
        article = article.upper()

        return article

    @staticmethod
    def normalize_unit(unit: str) -> str:
        """
        Нормализует единицу измерения.

        Args:
            unit: Исходная единица измерения

        Returns:
            Стандартизованная единица измерения
        """
        if not unit:
            return 'шт'

        # Приводим к нижнему регистру и убираем пробелы
        unit = str(unit).strip().lower()

        # Ищем в таблице соответствий
        normalized = DataNormalizer.UNIT_MAPPING.get(unit)

        # Если не нашли, возвращаем как есть (но lowercase)
        return normalized if normalized else unit

    @staticmethod
    def normalize_vendor_name(vendor: str) -> str:
        """
        Нормализует название вендора.

        Args:
            vendor: Исходное название вендора

        Returns:
            Стандартизованное название
        """
        if not vendor:
            return ""

        vendor_lower = vendor.strip().lower()

        # Проверяем таблицу маппинга
        mapped = DataNormalizer.VENDOR_NAME_MAPPING.get(vendor_lower)

        # Если есть маппинг - используем его, иначе uppercase
        return mapped if mapped else vendor.strip().upper()

    @staticmethod
    def is_price_on_request(price_str: str) -> bool:
        """
        Проверяет, является ли цена "по запросу" (заказная позиция).

        Args:
            price_str: Строковое представление цены

        Returns:
            True если это заказная позиция
        """
        if not price_str:
            return False

        price_lower = str(price_str).strip().lower()

        # Ключевые слова для заказных позиций
        keywords = [
            'запрос',
            'по запросу',
            'договор',
            'уточн',
            'уточнить',
            'under request',
            'на заказ',
        ]

        return any(keyword in price_lower for keyword in keywords)

    @staticmethod
    def clean_price_value(price_val: any) -> Optional[float]:
        """
        Очищает и конвертирует значение цены.

        Args:
            price_val: Значение цены (может быть str, float, int)

        Returns:
            float или None если это заказная позиция
        """
        if price_val is None:
            return None

        price_str = str(price_val).strip()

        # Проверяем на заказную позицию
        if DataNormalizer.is_price_on_request(price_str):
            return None  # None означает "заказная позиция"

        # Очищаем от всех символов кроме цифр, точки и запятой
        price_str = re.sub(r'[^\d,.]', '', price_str)

        # Заменяем запятую на точку
        price_str = price_str.replace(',', '.')

        try:
            return float(price_str) if price_str else 0.0
        except ValueError:
            return 0.0

    @staticmethod
    def normalize_description(description: str) -> str:
        """
        Нормализует описание товара.

        Args:
            description: Исходное описание

        Returns:
            Очищенное описание
        """
        if not description:
            return ""

        # Убираем лишние пробелы
        description = str(description).strip()
        description = re.sub(r'\s+', ' ', description)

        return description
