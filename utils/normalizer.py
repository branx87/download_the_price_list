import re


class ArticleNormalizer:
    """Класс для нормализации артикулов вендоров"""

    # Правила нормализации для каждого вендора
    RULES = {
        'KEAZ': lambda x: re.sub(r'\s+', '', x.strip().upper()),
        'OWEN': lambda x: re.sub(r'\s+', '', x.strip().upper()),
        'EKF': lambda x: re.sub(r'\s+', '', x.strip().upper()),
        'IEK': lambda x: re.sub(r'\s+', '', x.strip().upper()),
        'DKC': lambda x: re.sub(r'\s+', '', x.strip().upper()),
        'CHINT': lambda x: re.sub(r'\s+', '', x.strip().upper()),
    }

    def normalize(self, article: str, vendor: str = None) -> str:
        """
        Нормализует артикул.

        Args:
            article: Артикул для нормализации
            vendor: Название вендора (опционально)

        Returns:
            str: Нормализованный артикул
        """
        if not article:
            return ""

        article = str(article).strip()

        # Применяем вендор-специфичное правило
        if vendor and vendor in self.RULES:
            return self.RULES[vendor](article)

        # Общее правило: убираем лишние пробелы, верхний регистр
        return re.sub(r'\s+', '', article.upper())
