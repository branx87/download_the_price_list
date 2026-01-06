"""
Скрипт для миграции записей ОВЕН → OWEN в базе данных.

Обновляет все записи с Vendor='ОВЕН' на Vendor='OWEN' для консистентности.

Использование:
    python scripts/migrate_owen_vendor.py
"""

import sys
from pathlib import Path

# Добавляем корень проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings
from sqlalchemy import create_engine, text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def migrate_owen_vendor():
    """Обновляет все записи ОВЕН на OWEN"""
    database_url = settings.DATABASE_URL

    if 'sqlite' in database_url:
        database_url = database_url.replace('sqlite:///', 'sqlite:///') + '?timeout=30'

    engine = create_engine(database_url, connect_args={'timeout': 30} if 'sqlite' in database_url else {})

    try:
        with engine.connect() as connection:
            # Проверяем сколько записей нужно обновить
            count_query = text("SELECT COUNT(*) FROM Total_Price WHERE Vendor = 'ОВЕН'")
            result = connection.execute(count_query)
            count = result.scalar()

            logger.info(f"Найдено записей с Vendor='ОВЕН': {count}")

            if count == 0:
                logger.info("Нечего обновлять")
                return

            # Обновляем
            update_query = text("UPDATE Total_Price SET Vendor = 'OWEN' WHERE Vendor = 'ОВЕН'")
            result = connection.execute(update_query)
            connection.commit()

            logger.info(f"✅ Обновлено {result.rowcount} записей: ОВЕН → OWEN")

    except Exception as e:
        logger.error(f"❌ Ошибка миграции: {e}")
        raise


if __name__ == '__main__':
    print("=" * 60)
    print("МИГРАЦИЯ ДАННЫХ: ОВЕН → OWEN")
    print("=" * 60)
    print("")
    print("Этот скрипт обновит все записи с Vendor='ОВЕН' на Vendor='OWEN'")
    print("")

    response = input("Продолжить? (yes/no): ").strip().lower()

    if response in ['yes', 'y', 'да']:
        migrate_owen_vendor()
    else:
        print("Отменено")
