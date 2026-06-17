"""
Скрипт для восстановления позиций со статусом 'disappeared' обратно в активные.

Использование:
    python scripts/restore_disappeared.py [vendor]

Если vendor не указан, восстанавливает для всех вендоров.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.database.sql_repository import SqlRepository
from config.settings import settings


def main():
    vendor = sys.argv[1] if len(sys.argv) > 1 else None

    repo = SqlRepository(settings.DATABASE_URL)

    with repo.SessionLocal() as session:
        from sqlalchemy import text

        if vendor:
            # Восстановить конкретный вендор
            result = session.execute(text(
                "UPDATE Total_Price SET Status = 'active', updated_at = GETDATE() "
                "WHERE Vendor = :vendor AND Status = 'disappeared'"
            ), {'vendor': vendor})
            count = result.rowcount
            session.commit()
            print(f"✅ Восстановлено {count} позиций для {vendor}")
        else:
            # Восстановить все
            result = session.execute(text(
                "UPDATE Total_Price SET Status = 'active', updated_at = GETDATE() "
                "WHERE Status = 'disappeared'"
            ))
            count = result.rowcount
            session.commit()
            print(f"✅ Восстановлено {count} позиций (все вендоры)")


if __name__ == '__main__':
    main()
