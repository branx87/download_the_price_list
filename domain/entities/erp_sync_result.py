from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class ErpSyncResult:
    """Результат синхронизации с 1C-ERP"""

    total_received: int = 0
    added: int = 0
    updated: int = 0
    skipped_existing: int = 0
    skipped_duplicates: int = 0
    errors: int = 0
    duplicate_codes: List[str] = field(default_factory=list)
    error_details: List[str] = field(default_factory=list)

    # Детали для отчёта: что именно добавлено/привязано
    added_details: List[Dict[str, str]] = field(default_factory=list)
    linked_details: List[Dict[str, str]] = field(default_factory=list)

    @property
    def has_duplicates(self) -> bool:
        return len(self.duplicate_codes) > 0

    @property
    def has_errors(self) -> bool:
        return self.errors > 0
