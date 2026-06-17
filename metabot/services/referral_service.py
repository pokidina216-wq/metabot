"""
Сервис реферальной системы.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from metabot.models.referral import Referral, ReferralBonus
from metabot.models.user import User
from metabot.repositories.referral_repo import ReferralRepository, ReferralBonusRepository
from metabot.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

# Бонусы
BONUS_REGISTRATION = 1   # +1 запрос за регистрацию реферала
BONUS_PURCHASE = 5        # +5 запросов когда реферал покупает подписку


@dataclass
class ReferralStats:
    total_referrals: int
    activated: int
    total_bonuses: int
    referral_code: str
    referral_link: str


class ReferralService:
    """Бизнес-логика реферальной системы."""

    def __init__(self, session: AsyncSession, bot_username: str) -> None:
        self.session = session
        self.ref_repo = ReferralRepository(session)
        self.bonus_repo = ReferralBonusRepository(session)
        self.user_repo = UserRepository(session)
        self.bot_username = bot_username

    async def process_referral(self, new_user: User, referral_code: str) -> bool:
        """Обработать реферальную ссылку при регистрации."""
        referrer = await self.user_repo.get_by_referral_code(referral_code)
        if not referrer or referrer.id == new_user.id:
            return False

        # Проверить — не дубль ли
        existing = await self.ref_repo.get_by_referred(new_user.id)
        if existing:
            return False

        # Создать запись
        ref = await self.ref_repo.create(
            referrer_id=referrer.id,
            referred_id=new_user.id,
            is_activated=True,
        )

        # Начислить бонус за регистрацию
        await self.bonus_repo.create(
            user_id=referrer.id,
            referral_id=ref.id,
            bonus_type="registration",
            bonus_amount=BONUS_REGISTRATION,
            description=f"Регистрация реферала @{new_user.username or new_user.telegram_id}",
        )

        # Обновить баланс бонусов
        referrer.referral_bonus_balance += BONUS_REGISTRATION
        new_user.referred_by_id = referrer.id
        await self.session.flush()

        logger.info(
            "Referral processed: referrer=%d, referred=%d",
            referrer.id, new_user.id
        )
        return True

    async def process_purchase_bonus(self, buyer: User) -> None:
        """Начислить бонус рефереру при покупке подписки."""
        if not buyer.referred_by_id:
            return

        ref = await self.ref_repo.get_by_referred(buyer.id)
        if not ref:
            return

        referrer = await self.user_repo.get_by_id(ref.referrer_id)
        if not referrer:
            return

        await self.bonus_repo.create(
            user_id=referrer.id,
            referral_id=ref.id,
            bonus_type="purchase",
            bonus_amount=BONUS_PURCHASE,
            description=f"Покупка подписки рефералом @{buyer.username or buyer.telegram_id}",
        )
        referrer.referral_bonus_balance += BONUS_PURCHASE
        await self.session.flush()

        logger.info(
            "Referral purchase bonus: referrer=%d (+%d)",
            referrer.id, BONUS_PURCHASE
        )

    async def get_stats(self, user: User) -> ReferralStats:
        """Получить статистику рефералов пользователя."""
        total = await self.ref_repo.count_referrals(user.id)
        activated = await self.ref_repo.count_activated(user.id)
        bonuses = await self.bonus_repo.total_bonuses(user.id)

        return ReferralStats(
            total_referrals=total,
            activated=activated,
            total_bonuses=bonuses,
            referral_code=user.referral_code or "",
            referral_link=f"https://t.me/{self.bot_username}?start=ref_{user.referral_code}",
        )
