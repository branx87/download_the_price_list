from dataclasses import dataclass, field
from datetime import datetime
from typing import List


@dataclass
class SyncResult:
    """Результат синхронизации прайс-листа"""

    vendor: str
    success: bool
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime = field(default_factory=datetime.now)

    # Статистика
    total_items: int = 0
    new_items: int = 0
    updated_items: int = 0
    disappeared_items: int = 0

    # Детали
    error_message: str = ""
    file_path: str = ""

    @property
    def duration_seconds(self) -> float:
        """Длительность синхронизации в секундах"""
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def changes_count(self) -> int:
        """Общее количество изменений"""
        return self.new_items + self.updated_items + self.disappeared_items

    def __str__(self):
        status = "✅" if self.success else "❌"
        
        # ИСПРАВЬ ЭТО:
        if self.finished_at and self.started_at:
            duration = (self.finished_at - self.started_at).total_seconds()  # ← Преобразуй в секунды
        else:
            duration = 0
        
        return (
            f"{status} {self.vendor}: "
            f"total={self.total_items}, "
            f"new={self.new_items}, "
            f"updated={self.updated_items}, "
            f"disappeared={self.disappeared_items}, "
            f"time={duration:.1f}s"
        )