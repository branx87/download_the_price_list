"""
Инициализация базы данных - создание таблиц
"""
from adapters.database.connection import engine, Base
from adapters.database.models import TotalPrice, SyncHistory
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_database():
    """Создает все таблицы в базе данных"""
    try:
        logger.info("Создание таблиц в базе данных...")
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Таблицы успешно созданы!")

        # Проверяем
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        logger.info(f"Созданные таблицы: {tables}")

    except Exception as e:
        logger.error(f"❌ Ошибка создания таблиц: {e}")
        raise

if __name__ == "__main__":
    init_database()
