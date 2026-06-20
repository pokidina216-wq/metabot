"""
Базовый класс для OSINT-источника.

Все встроенные и пользовательские источники наследуют от него.

Изменения (Stage 2 → audit):
- В OsintFinding добавлено поле `urls: List[str]` — список РЕАЛЬНО проверенных
  и подтверждённых ссылок. Это требуется поиску по нику, который теперь
  возвращает не «название платформы», а конкретные URL.
- `format_text` рендерит блок URL с кликабельными ссылками.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class OsintFinding:
    """Единичная находка из источника."""
    source_name: str
    found: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    # Список подтверждённых URL (реально проверенных HTTP-запросом).
    # Пустой → нет находок; именно по этому полю формируется ответ
    # «По нику», см. metabot/handlers/osint_handler.py.
    urls: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def format_text(self) -> str:
        if self.error:
            return f"⚠️ <b>{self.source_name}:</b> Ошибка — {self.error}"
        if not self.found and not self.urls:
            return f"❌ <b>{self.source_name}:</b> Не найдено"

        lines = [f"✅ <b>{self.source_name}:</b>"]
        for key, value in self.data.items():
            if value:
                lines.append(f"  • <b>{key}:</b> {value}")
        # Блок реально найденных URL.
        if self.urls:
            for url in self.urls:
                lines.append(f"  🔗 {url}")
        return "\n".join(lines)


class BaseOsintSource(abc.ABC):
    """Абстрактный интерфейс для OSINT-источника."""

    name: str = "Unknown"
    category: str = "general"  # phone / email / username / domain / ip / face / nick

    @abc.abstractmethod
    async def search(self, query: str) -> OsintFinding:
        """Выполнить поиск и вернуть результат."""
        ...
