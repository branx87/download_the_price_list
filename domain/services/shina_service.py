import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MATERIAL_ALIASES = {
    'медь':          'медь',
    'медный':        'медь',
    'copper':        'медь',
    'алюм':          'алюм',
    'алюминий':      'алюм',
    'алюминиевый':   'алюм',
    'aluminum':      'алюм',
    'aluminium':     'алюм',
}


class ShinaService:
    """Сервис управления прайсом вендора ШИНА."""

    def __init__(self, repository, parser):
        self.repository = repository
        self.parser = parser

    def load_from_excel(self, file_path: str | Path) -> dict:
        """Парсит Excel, загружает shina_config, пересчитывает Total_Price.

        Returns dict: {loaded, updated, copper_price_per_kg, alum_price_per_kg}
        """
        parsed = self.parser.parse(file_path)
        copper_price = parsed['copper_price_per_kg']
        alum_price   = parsed['alum_price_per_kg']

        for item in parsed['items']:
            item['price_per_kg'] = copper_price if item['material'] == 'медь' else alum_price

        loaded  = self.repository.upsert_shina_config(parsed['items'])
        updated = self.repository.recalculate_shina_to_total_price()

        logger.info("[SHINA] load_from_excel: конфигов=%d, Total_Price=%d", loaded, updated)
        return {
            'loaded':              loaded,
            'updated':             updated,
            'copper_price_per_kg': copper_price,
            'alum_price_per_kg':   alum_price,
        }

    def update_price(self, material_input: str, price_per_kg: float) -> int:
        """Обновляет price_per_kg для материала и пересчитывает Total_Price.

        Returns: количество обновлённых позиций в Total_Price (0 если материал не найден).
        """
        material = MATERIAL_ALIASES.get(material_input.lower())
        if material is None:
            raise ValueError(
                f"Неизвестный материал: {material_input!r}. "
                f"Допустимые: {', '.join(MATERIAL_ALIASES)}"
            )

        updated_config = self.repository.update_shina_price_per_kg(material, price_per_kg)
        if updated_config == 0:
            logger.warning("[SHINA] update_price: material=%r не найден в shina_config", material)
            return 0

        updated = self.repository.recalculate_shina_to_total_price(material=material)
        logger.info("[SHINA] update_price: material=%r price_per_kg=%.2f Total_Price=%d",
                    material, price_per_kg, updated)
        return updated

    def get_current_prices(self) -> dict:
        """Возвращает {material: price_per_kg} из shina_config."""
        return self.repository.get_shina_prices()

    def get_config_count(self) -> int:
        return self.repository.get_shina_config_count()
