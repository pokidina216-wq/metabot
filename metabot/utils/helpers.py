"""
Вспомогательные утилиты.
"""
from __future__ import annotations

import html


def escape_html(text: str) -> str:
    """Экранировать HTML-спецсимволы для Telegram."""
    return html.escape(str(text))


def truncate(text: str, max_len: int = 200, suffix: str = "...") -> str:
    """Обрезать строку до max_len символов."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


def format_number(n: int | float) -> str:
    """Форматировать число с разделителями: 12345 → 12,345."""
    return f"{n:,.0f}"


def format_size(size_bytes: int) -> str:
    """Человекочитаемый размер файла."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
