"""Добавление индексов для ускорения работы с БД"""
import sqlite3
from pathlib import Path

db_path = Path(__file__).parent / "prices.db"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Добавляем индексы
cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendor_article ON Total_Price(Vendor, Part_Num)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendor ON Total_Price(Vendor)")

conn.commit()
conn.close()

print("✅ Индексы добавлены")
