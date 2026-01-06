import logging
import sys  # ← ПЕРЕМЕСТИЛ СЮДА
from pathlib import Path
import codecs  # ← ПЕРЕМЕСТИЛ СЮДА

from config.settings import settings
from utils.normalizer import ArticleNormalizer
from vendors.registry import VendorRegistry
from adapters.database.sql_repository import SqlRepository
from domain.services.sync_service import SyncService


# Для Windows консоли - настраиваем ДО basicConfig
if sys.platform == 'win32':
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, errors='replace')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, errors='replace')


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/sync.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


def create_sync_service(vendor: str) -> SyncService:
    """
    Создает сервис синхронизации для вендора.

    Dependency Injection: собираем все зависимости и создаем сервис.
    """
    # 1. Создаем общие компоненты
    normalizer = ArticleNormalizer()
    registry = VendorRegistry(settings.PRICE_FILES_DIR, normalizer)
    repository = SqlRepository(settings.DATABASE_URL)

    # 2. Создаем компоненты для конкретного вендора
    downloader = registry.create_downloader(vendor)
    parser = registry.create_parser(vendor)

    # 3. Собираем сервис
    service = SyncService(
        downloader=downloader,
        parser=parser,
        repository=repository,
        price_change_threshold=settings.PRICE_CHANGE_THRESHOLD
    )

    return service


def sync_one_vendor(vendor: str):
    """Синхронизация одного вендора"""
    logger.info(f"{'='*60}")
    logger.info(f"Запуск синхронизации: {vendor}")
    logger.info(f"{'='*60}")

    try:
        service = create_sync_service(vendor)
        result = service.sync_vendor(vendor)
        
        logger.info(str(result))
        
        # ПРОВЕРЬ ЭТО:
        if result.success:
            return result  # ← Вместо sys.exit(0)
        else:
            logger.error(f"Ошибка: {result.error_message}")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)

def sync_all_vendors():
    """Синхронизация всех вендоров"""
    logger.info("Запуск синхронизации всех вендоров")  # Убрал эмодзи
    logger.info(f"{'='*60}")

    normalizer = ArticleNormalizer()
    registry = VendorRegistry(settings.PRICE_FILES_DIR, normalizer)
    vendors = registry.get_vendor_names()

    results = []
    for vendor in vendors:
        result = sync_one_vendor(vendor)
        results.append(result)

    # Итоговая статистика
    logger.info(f"\n{'='*60}")
    logger.info("ИТОГОВАЯ СТАТИСТИКА")  # Убрал эмодзи
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
        # Синхронизация конкретного вендора
        vendor = sys.argv[1].upper()
        sync_one_vendor(vendor)
    else:
        # Синхронизация всех вендоров
        sync_all_vendors()