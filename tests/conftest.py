"""
Общие фикстуры тестов.

Поднимаем эфемерный PostgreSQL (pgserver) в памяти, применяем все миграции
Alembic до head, отдаём async-фабрику сессий. Каждый модуль использует свежую
БД, чтобы тесты не влияли друг на друга.
"""
from __future__ import annotations

import os
import pathlib
import tempfile

import pytest
import pytest_asyncio

WORK = pathlib.Path(__file__).resolve().parent.parent
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("OWNER_ID", "7221385215")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

OWNER_ID = 7221385215


def _apply_migrations(dsn: str) -> None:
    from alembic.config import Config
    from alembic import command

    cfg = Config(str(WORK / "alembic.ini"))
    cfg.set_main_option("script_location", str(WORK / "alembic"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def pg_dsn():
    import pgserver

    sockdir = tempfile.mkdtemp(prefix="pg_test_")
    srv = pgserver.get_server(sockdir)
    dsn = f"postgresql+asyncpg://postgres@/postgres?host={sockdir}"
    os.environ["DATABASE_URL"] = dsn
    _apply_migrations(dsn)
    try:
        yield dsn
    finally:
        srv.cleanup()


@pytest_asyncio.fixture
async def session_factory(pg_dsn):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    engine = create_async_engine(pg_dsn)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield Session
    finally:
        await engine.dispose()
