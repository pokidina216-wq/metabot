"""
Базовый класс для OSINT-источника.
Все встроенные и пользовательские источники наследуют от него.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class OsintFinding:
    """Единичная находка из источника."""
    source_name: str
    found: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def format_text(self) -> str:
        if self.error:
            return f"⚠️ <b>{self.source_name}:</b> Ошибка — {self.error}"
        if not self.found:
            return f"❌ <b>{self.source_name}:</b> Не найдено"

        lines = [f"✅ <b>{self.source_name}:</b>"]
        for key, value in self.data.items():
            if value:
                lines.append(f"  • <b>{key}:</b> {value}")
        return "\n".join(lines)


class BaseOsintSource(abc.ABC):
    """Абстрактный интерфейс для OSINT-источника."""

    name: str = "Unknown"
    category: str = "general"  # phone / email / username / domain / ip / face

    @abc.abstractmethod
    async def search(self, query: str) -> OsintFinding:
        """Выполнить поиск и вернуть результат."""
        ...
