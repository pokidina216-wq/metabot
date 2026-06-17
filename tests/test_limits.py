"""Атомарные дневные лимиты (Stage 4): нет гонки TOCTOU."""
import asyncio

import pytest
from sqlalchemy import select

from metabot.models.user import User, UserRole
from metabot.repositories.user_repo import UserRepository
from metabot.services.user_service import UserService

LIMIT = 5


@pytest.mark.asyncio
async def test_consume_respects_limit(session_factory):
    async with session_factory() as s:
        s.add(User(telegram_id=501, username="u1", role=UserRole.USER))
        await s.commit()
        uid = (await s.execute(select(User).where(User.telegram_id == 501))).scalar_one().id

    async with session_factory() as s:
        repo = UserRepository(s)
        oks = []
        for _ in range(LIMIT + 3):
            allowed, remaining = await repo.try_consume_daily(uid, LIMIT)
            oks.append(allowed)
            await s.commit()
        assert oks.count(True) == LIMIT
        assert oks.count(False) == 3


@pytest.mark.asyncio
async def test_no_overspend_under_concurrency(session_factory):
    """Параллельные списания не должны превысить лимит."""
    async with session_factory() as s:
        s.add(User(telegram_id=502, username="u2", role=UserRole.USER))
        await s.commit()
        uid = (await s.execute(select(User).where(User.telegram_id == 502))).scalar_one().id

    async def consume():
        async with session_factory() as s:
            ok, _ = await UserRepository(s).try_consume_daily(uid, LIMIT)
            await s.commit()
            return ok

    results = await asyncio.gather(*[consume() for _ in range(30)])
    assert sum(1 for r in results if r) == LIMIT  # ровно LIMIT успешных

    async with session_factory() as s:
        u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
        assert u.daily_requests_used == LIMIT  # не превышен


@pytest.mark.asyncio
async def test_service_uses_free_limit(session_factory):
    async with session_factory() as s:
        s.add(User(telegram_id=503, username="u3", role=UserRole.USER))
        await s.commit()
        user = (await s.execute(select(User).where(User.telegram_id == 503))).scalar_one()
        svc = UserService(s)
        allowed, remaining = await svc.check_and_increment(user)
        await s.commit()
        assert allowed and remaining == 4  # free limit = 5
