"""
Репозиторий реферальной системы.
"""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import func, select

from metabot.models.referral import Referral, ReferralBonus
from .base import BaseRepository


class ReferralRepository(BaseRepository[Referral]):
    model = Referral

    async def get_by_referred(self, referred_id: int) -> Referral | None:
        return await self.get_one(referred_id=referred_id)

    async def count_referrals(self, referrer_id: int) -> int:
        return await self.count(referrer_id=referrer_id)

    async def count_activated(self, referrer_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(Referral)
            .where(
                Referral.referrer_id == referrer_id,
                Referral.is_activated.is_(True),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_referrals_list(
        self, referrer_id: int, offset: int = 0, limit: int = 20
    ) -> Sequence[Referral]:
        return await self.get_many(
            referrer_id=referrer_id,
            offset=offset,
            limit=limit,
            order_by=Referral.created_at.desc(),
        )


class ReferralBonusRepository(BaseRepository[ReferralBonus]):
    model = ReferralBonus

    async def total_bonuses(self, user_id: int) -> int:
        stmt = (
            select(func.coalesce(func.sum(ReferralBonus.bonus_amount), 0))
            .where(ReferralBonus.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()
