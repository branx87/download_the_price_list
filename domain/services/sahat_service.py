import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SahatService:
    """Сервис загрузки конфигурации Selectric из Excel-прайса Sahat DDP."""

    def __init__(self, repository, parser):
        self.repository = repository
        self.parser = parser

    def load_from_excel(self, file_path: str | Path) -> dict:
        """Парсит Excel, загружает обе конфиг-таблицы.

        Returns: {'mccb_loaded': int, 'acb_loaded': int}
        """
        parsed = self.parser.parse(file_path)
        mccb_loaded = self.repository.upsert_selectric_mccb(parsed['mccb'])
        acb_loaded  = self.repository.upsert_selectric_acb(parsed['acb'])
        logger.info("[SELECTRIC] load_from_excel: mccb=%d acb=%d", mccb_loaded, acb_loaded)
        return {'mccb_loaded': mccb_loaded, 'acb_loaded': acb_loaded}
