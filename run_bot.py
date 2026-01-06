#!/usr/bin/env python3
"""Запуск Telegram бота"""
import sys
import logging
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from bot.main import main

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

if __name__ == '__main__':
    print("=" * 60)
    print("  TELEGRAM БОТ СИНХРОНИЗАЦИИ ПРАЙСОВ")
    print("=" * 60)
    print()
    main()
