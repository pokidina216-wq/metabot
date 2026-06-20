"""
Публичное API OSINT-подсистемы.

`get_osint_engine()` возвращает синглтон с уже зарегистрированными
встроенными источниками. Это используется хендлерами (см. `handlers/
osint_handler.py`, ветка «По нику»).

До аудита движок никогда не инициализировался — хендлеры показывали
статические ссылки на агрегаторы вместо реальной проверки. Теперь
поиск по нику реально проверяет платформы и возвращает только
подтверждённые URL.
"""
from __future__ import annotations

from functools import lru_cache

from .base_source import BaseOsintSource, OsintFinding
from .engine import OsintEngine


@lru_cache(maxsize=1)
def get_osint_engine() -> OsintEngine:
    """Синглтон OSINT-движка с зарегистрированными источниками."""
    engine = OsintEngine()
    # Импортируем здесь, чтобы избежать циклов и сохранить чистую модульность.
    from .sources import (
        DnsSource,
        EmailBreachSource,
        IpInfoSource,
        UsernameOsintSource,
        WhoisSource,
    )
    engine.register(UsernameOsintSource())
    engine.register(WhoisSource())
    engine.register(DnsSource())
    engine.register(IpInfoSource())
    engine.register(EmailBreachSource())
    return engine


__all__ = [
    "OsintEngine",
    "BaseOsintSource",
    "OsintFinding",
    "get_osint_engine",
]
