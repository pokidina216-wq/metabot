"""
Репозиторий платежей.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import func, select

from metabot.models.subscription import Payment, PaymentStatus
from .base import BaseRepository


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    async def get_user_payments(
        self, user_id: int, offset: int = 0, limit: int = 10
    ) -> Sequence[Payment]:
        stmt = (
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def total_revenue_since(self, since: datetime) -> float:
        stmt = (
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(
                Payment.status == PaymentStatus.COMPLETED,
                Payment.created_at >= since,
            )
        )
        result = await self.session.execute(stmt)
        return float(result.scalar_one())

    async def count_completed_since(self, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(Payment)
            .where(
                Payment.status == PaymentStatus.COMPLETED,
                Payment.created_at >= since,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()
