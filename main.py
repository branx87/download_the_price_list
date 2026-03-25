import logging
import sys
from pathlib import Path

from config.logging_config import setup_logging
from config.settings import settings

setup_logging(settings.LOG_DIR)

from utils.normalizer import ArticleNormalizer
from vendors.registry import VendorRegistry
from adapters.database.sql_repository import SqlRepository
from domain.services.sync_service import SyncService
from domain.services.report_service import ReportService


logger = logging.getLogger(__name__)


def create_sync_service(vendor: str) -> SyncService:
    """Создает сервис синхронизации для вендора (Dependency Injection)."""
    normalizer = ArticleNormalizer()
    registry = VendorRegistry(settings.PRICE_FILES_DIR, normalizer)
    repository = SqlRepository(settings.DATABASE_URL)
    report_service = ReportService(settings.PROJECT_ROOT / "reports")

    downloader = registry.create_downloader(vendor)
    parser = registry.create_parser(vendor)

    return SyncService(
        downloader=downloader,
        parser=parser,
        repository=repository,
        price_change_threshold=settings.PRICE_CHANGE_THRESHOLD,
        report_service=report_service
    )


def sync_one_vendor(vendor: str):
    """Синхронизация одного вендора"""
    logger.info(f"{'='*60}")
    logger.info(f"Запуск синхронизации: {vendor}")
    logger.info(f"{'='*60}")

    try:
        service = create_sync_service(vendor)
        result = service.sync_vendor(vendor)

        logger.info(str(result))

        if result.success:
            return result
        else:
            logger.error(f"Ошибка: {result.error_message}")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)

def sync_all_vendors():
    """Синхронизация всех вендоров"""
    logger.info("Запуск синхронизации всех вендоров")
    logger.info(f"{'='*60}")

    normalizer = ArticleNormalizer()
    registry = VendorRegistry(settings.PRICE_FILES_DIR, normalizer)
    vendors = registry.get_vendor_names()

    results = []
    for vendor in vendors:
        result = sync_one_vendor(vendor)
        results.append(result)

    logger.info(f"\n{'='*60}")
    logger.info("ИТОГОВАЯ СТАТИСТИКА")
    logger.info(f"{'='*60}")

    success_count = sum(1 for r in results if r.success)
    total_new = sum(r.new_items for r in results)
    total_updated = sum(r.updated_items for r in results)
    total_disappeared = sum(r.disappeared_items for r in results)

    logger.info(f"Успешно: {success_count}/{len(results)}")
    logger.info(f"Новых позиций: {total_new}")
    logger.info(f"Обновлено: {total_updated}")
    logger.info(f"Исчезло: {total_disappeared}")

    return results


if __name__ == '__main__':
    if len(sys.argv) > 1:
        vendor = sys.argv[1].upper()
        sync_one_vendor(vendor)
    else:
        sync_all_vendors()
