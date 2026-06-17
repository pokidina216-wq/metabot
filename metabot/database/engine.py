"""
Асинхронный движок PostgreSQL + фабрика сессий.
Пул подключений настроен для production нагрузки.
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from metabot.configs import get_settings
from metabot.models.base import Base

logger = logging.getLogger(__name__)

settings = get_settings()

# ── Движок с пулом подключений ────────────────────────────────
async_engine: AsyncEngine = create_async_engine(
    settings.db_url,
    echo=False,
    pool_size=20,             # Базовый размер пула
    max_overflow=30,          # Допускаем до 50 одновременных подключений
    pool_pre_ping=True,       # Проверка живости перед выдачей
    pool_recycle=3600,        # Переподключение каждый час
    pool_timeout=30,          # Таймаут ожидания свободного подключения
)

# ── Фабрика сессий ─────────────────────────────────────────────
async_session_factory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency-генератор сессий."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Создание таблиц (для первичной инициализации; в prod — Alembic)."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured (create_all)")
