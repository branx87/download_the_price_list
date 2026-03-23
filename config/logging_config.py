"""Централизованная настройка логирования для всего проекта."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured = False


def setup_logging(log_dir: Path | str = "logs", level: int = logging.INFO) -> None:
    """Настраивает логирование один раз для всего приложения.

    - RotatingFileHandler: sync.log (5 МБ, 3 бэкапа)
    - StreamHandler: stdout
    - Формат: timestamp - name - level - message
    """
    global _configured
    if _configured:
        return
    _configured = True

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Файл с ротацией
    file_handler = RotatingFileHandler(
        log_dir / "sync.log",
        maxBytes=5 * 1024 * 1024,  # 5 МБ
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    # Консоль
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    # Приглушаем шумные библиотеки
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)
