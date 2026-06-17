"""
Репозиторий подписок и тарифных планов.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select

from metabot.models.subscription import (
    Plan, Subscription, SubscriptionRequest, SubscriptionRequestStatus,
)
from .base import BaseRepository


class PlanRepository(BaseRepository[Plan]):
    model = Plan

    async def get_active_plans(self) -> Sequence[Plan]:
        stmt = (
            select(Plan)
            .where(Plan.is_active.is_(True))
            .order_by(Plan.sort_order, Plan.price_usd)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_slug(self, slug: str) -> Optional[Plan]:
        return await self.get_one(slug=slug)


class SubscriptionRepository(BaseRepository[Subscription]):
    model = Subscription

    async def get_active_subscription(self, user_id: int) -> Optional[Subscription]:
        """Получить текущую активную подписку пользователя."""
        now = datetime.now(timezone.utc)
        stmt = (
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.is_active.is_(True),
                Subscription.expires_at > now,
            )
            .order_by(Subscription.expires_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_history(
        self, user_id: int, offset: int = 0, limit: int = 10
    ) -> Sequence[Subscription]:
        stmt = (
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def deactivate_expired(self) -> int:
        """Деактивация просроченных подписок. Вызывается шедулером."""
        now = datetime.now(timezone.utc)
        from sqlalchemy import update
        stmt = (
            update(Subscription)
            .where(
                Subscription.is_active.is_(True),
                Subscription.expires_at <= now,
            )
            .values(is_active=False)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount


class SubscriptionRequestRepository(BaseRepository[SubscriptionRequest]):
    model = SubscriptionRequest

    async def get_pending_for_user(self, user_id: int) -> Optional[SubscriptionRequest]:
        """Активная (ожидающая решения) заявка пользователя, если есть."""
        stmt = (
            select(SubscriptionRequest)
            .where(
                SubscriptionRequest.user_id == user_id,
                SubscriptionRequest.status == SubscriptionRequestStatus.PENDING,
            )
            .order_by(SubscriptionRequest.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_pending(self, limit: int = 50) -> Sequence[SubscriptionRequest]:
        stmt = (
            select(SubscriptionRequest)
            .where(SubscriptionRequest.status == SubscriptionRequestStatus.PENDING)
            .order_by(SubscriptionRequest.created_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
