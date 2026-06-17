"""Ручная монетизация (Stage 1): заявка → апрув Owner, идемпотентность, анти-дубль."""
import pytest
from sqlalchemy import select

from metabot.models.user import User, UserRole
from metabot.models.subscription import Plan
from metabot.services.subscription_service import (
    SubscriptionService, DuplicateRequestError,
)

OWNER = 7221385215


async def _seed(session_factory):
    async with session_factory() as s:
        s.add(User(telegram_id=601, username="buyer", role=UserRole.USER))
        s.add(Plan(name="Premium", slug="premium", price_usd=3, price_stars=150,
                   duration_days=30, daily_request_limit=50, osint_enabled=True,
                   is_active=True, sort_order=1))
        await s.commit()


@pytest.mark.asyncio
async def test_full_subscription_flow(session_factory):
    await _seed(session_factory)
    # create request
    async with session_factory() as s:
        svc = SubscriptionService(s)
        user = (await s.execute(select(User).where(User.telegram_id == 601))).scalar_one()
        plan = (await s.execute(select(Plan).where(Plan.slug == "premium"))).scalar_one()
        req = await svc.create_request(user, plan)
        await s.commit()
        req_id = req.id

    # anti-duplicate
    async with session_factory() as s:
        svc = SubscriptionService(s)
        user = (await s.execute(select(User).where(User.telegram_id == 601))).scalar_one()
        plan = (await s.execute(select(Plan).where(Plan.slug == "premium"))).scalar_one()
        with pytest.raises(DuplicateRequestError):
            await svc.create_request(user, plan)

    # approve
    async with session_factory() as s:
        svc = SubscriptionService(s)
        outcome, req = await svc.approve_request(req_id, OWNER)
        await s.commit()
        assert outcome == "approved"

    # idempotent re-approve
    async with session_factory() as s:
        svc = SubscriptionService(s)
        outcome, _ = await svc.approve_request(req_id, OWNER)
        assert outcome in ("already", "not_pending")


@pytest.mark.asyncio
async def test_reject_flow(session_factory):
    async with session_factory() as s:
        s.add(User(telegram_id=602, username="b2", role=UserRole.USER))
        await s.commit()
    async with session_factory() as s:
        svc = SubscriptionService(s)
        user = (await s.execute(select(User).where(User.telegram_id == 602))).scalar_one()
        plan = (await s.execute(select(Plan).where(Plan.slug == "premium"))).scalar_one()
        req = await svc.create_request(user, plan)
        await s.commit()
        rid = req.id
    async with session_factory() as s:
        svc = SubscriptionService(s)
        outcome, _ = await svc.reject_request(rid, OWNER)
        await s.commit()
        assert outcome == "rejected"
