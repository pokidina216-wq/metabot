"""
Настройка логирования на базе structlog.

Цели:
- Структурированные JSON-логи в проде (Railway/Docker собирают stdout) →
  удобно фильтровать и алертить.
- Читаемый цветной вывод в dev (log_format="console").
- Маскирование секретов (bot token, пароли, ключи) в любом выводе.
- Единая точка: stdlib logging (aiogram, sqlalchemy, apscheduler) проходит
  через тот же processor-pipeline.
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

# Паттерны, которые маскируем в сообщениях логов (на всякий случай —
# секреты не должны попадать в логи вообще, это «последний рубеж»).
_SECRET_PATTERNS = [
    re.compile(r"(\d{6,}:[A-Za-z0-9_-]{30,})"),          # Telegram bot token
    re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^@\s]+@)"),  # creds в DB URL
    re.compile(r"(redis://[^@\s]+@)"),                    # creds в Redis URL
]


def _mask_secrets(_logger: Any, _method: str, event_dict: dict) -> dict:
    """Processor: маскирует секреты в поле event и строковых значениях."""
    def scrub(value: str) -> str:
        for pat in _SECRET_PATTERNS:
            value = pat.sub("***REDACTED***", value)
        return value

    for key, val in list(event_dict.items()):
        if isinstance(val, str):
            event_dict[key] = scrub(val)
    return event_dict


def setup_logging(level: str = "INFO", log_format: str = "json") -> None:
    """Настроить structlog + stdlib logging на stdout.

    Args:
        level: уровень корневого логгера (INFO/DEBUG/...).
        log_format: "json" (прод) или "console" (читаемый dev-вывод).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _mask_secrets,
    ]

    if log_format == "console":
        renderer: Any = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    else:
        renderer = structlog.processors.JSONRenderer()

    # structlog-логгеры
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Мост: stdlib logging (aiogram/sqlalchemy/apscheduler/uvicorn) →
    # тот же рендер через ProcessorFormatter.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Снижаем шум от библиотек
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Удобный доступ к structlog-логгеру."""
    return structlog.get_logger(name)
