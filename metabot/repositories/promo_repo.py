"""
Репозиторий промокодов.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, func

from metabot.models.promo_code import PromoCode
from .base import BaseRepository


class PromoCodeRepository(BaseRepository[PromoCode]):
    model = PromoCode

    async def get_by_code(self, code: str) -> PromoCode | None:
        """Найти промокод по строковому коду (case-insensitive)."""
        stmt = select(PromoCode).where(
            func.upper(PromoCode.code) == code.upper().strip()
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_by_code(self, code: str) -> PromoCode | None:
        """Найти активный, неистёкший, не исчерпанный промокод."""
        now = datetime.now(timezone.utc)
        stmt = (
            select(PromoCode)
            .where(
                func.upper(PromoCode.code) == code.upper().strip(),
                PromoCode.is_active.is_(True),
            )
        )
        result = await self.session.execute(stmt)
        promo = result.scalar_one_or_none()
        if not promo:
            return None
        # Проверить срок
        if promo.expires_at and promo.expires_at < now:
            return None
        # Проверить лимит использований
        if promo.max_uses > 0 and promo.used_count >= promo.max_uses:
            return None
        return promo

    async def list_active(self, limit: int = 50) -> Sequence[PromoCode]:
        """Все активные промокоды."""
        stmt = (
            select(PromoCode)
            .where(PromoCode.is_active.is_(True))
            .order_by(PromoCode.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def increment_usage(self, promo: PromoCode) -> None:
        """Увеличить счётчик использований."""
        promo.used_count += 1
        await self.session.flush()

    async def deactivate(self, promo_id: int) -> bool:
        """Деактивировать промокод."""
        promo = await self.get_by_id(promo_id)
        if not promo:
            return False
        promo.is_active = False
        await self.session.flush()
        return True
