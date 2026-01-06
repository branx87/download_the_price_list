"""
Инициализация базы данных - создание таблиц
"""
import sys
from pathlib import Path

# Добавляем текущую папку в путь
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import declarative_base
import os
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загружаем .env
load_dotenv()

# Получаем DATABASE_URL
database_url = os.getenv('DATABASE_URL', 'sqlite:///./prices.db')
logger.info(f"DATABASE_URL: {database_url}")

# Создаём engine
engine = create_engine(database_url, echo=True)

# Импортируем модели
try:
    from adapters.database.models import Base
    logger.info("Модели импортированы из adapters.database.models")
except ImportError:
    try:
        from database.models import Base
        logger.info("Модели импортированы из database.models")
    except ImportError:
        logger.error("Не удалось импортировать модели!")
        logger.info("Попробуем создать вручную...")

        # Создаём Base вручную
        Base = declarative_base()

        from sqlalchemy import Column, String, Float, DateTime, Integer
        from datetime import datetime

        class TotalPrice(Base):
            __tablename__ = 'Total_Price'

            Vendor = Column(String(50), primary_key=True)
            Part_Num = Column(String(100), primary_key=True)
            Descr = Column(String(500))
            Price = Column(Float)
            Units = Column(String(20))
            Storage = Column(String(100))
            Status = Column(String(20), default='active')
            Last_Updated = Column(DateTime, default=datetime.now)

        class SyncHistory(Base):
            __tablename__ = 'Sync_History'

            ID = Column(Integer, primary_key=True, autoincrement=True)
            Vendor = Column(String(50))
            Sync_Date = Column(DateTime, default=datetime.now)
            Total_Records = Column(Integer)
            New_Items = Column(Integer)
            Disappeared_Items = Column(Integer)
            Status = Column(String(20))
            Message = Column(String(500))

def init_database():
    """Создает все таблицы в базе данных"""
    try:
        logger.info("Создание таблиц в базе данных...")
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Таблицы успешно созданы!")

        # Проверяем
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        logger.info(f"Созданные таблицы: {tables}")

        return True

    except Exception as e:
        logger.error(f"❌ Ошибка создания таблиц: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = init_database()
    if success:
        print("\n✅ База данных инициализирована!")
        print("Теперь можешь запустить: python main.py KEAZ")
    else:
        print("\n❌ Ошибка инициализации БД")
