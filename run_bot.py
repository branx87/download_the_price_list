#!/usr/bin/env python3
"""Запуск Telegram бота"""
import asyncio
import sys
import logging
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

log_dir = project_root / 'logs'
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'sync.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)

# Подавляем шумные HTTP-логи
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)

from bot.main import main

if __name__ == '__main__':
    print("=" * 60)
    print("  TELEGRAM БОТ СИНХРОНИЗАЦИИ ПРАЙСОВ")
    print("=" * 60)
    print()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")
