"""
Сервис подписок — покупка, продление, отмена.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from metabot.models.subscription import (
    Plan, Subscription, Payment, PaymentStatus,
    SubscriptionRequest, SubscriptionRequestStatus,
)
from metabot.models.user import User
from metabot.repositories.subscription_repo import (
    PlanRepository, SubscriptionRepository, SubscriptionRequestRepository,
)
from metabot.repositories.payment_repo import PaymentRepository
from metabot.repositories.promo_repo import PromoCodeRepository
from metabot.models.promo_code import PromoCode

logger = logging.getLogger(__name__)


class DuplicateRequestError(Exception):
    """У пользователя уже есть заявка в статусе pending."""

    def __init__(self, existing: SubscriptionRequest) -> None:
        self.existing = existing
        super().__init__("Pending request already exists")


class SubscriptionService:
    """Бизнес-логика подписок."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.plan_repo = PlanRepository(session)
        self.sub_repo = SubscriptionRepository(session)
        self.pay_repo = PaymentRepository(session)
        self.req_repo = SubscriptionRequestRepository(session)
        self.promo_repo = PromoCodeRepository(session)

    async def get_plans(self) -> Sequence[Plan]:
        return await self.plan_repo.get_active_plans()

    async def get_plan_by_slug(self, slug: str) -> Plan | None:
        return await self.plan_repo.get_by_slug(slug)

    async def get_active_sub(self, user_id: int) -> Subscription | None:
        return await self.sub_repo.get_active_subscription(user_id)

    async def activate_subscription(
        self,
        user: User,
        plan: Plan,
        payment_method: str,
        provider_payment_id: str | None = None,
    ) -> Subscription:
        """Создать подписку и запись о платеже."""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=plan.duration_days)

        # Деактивировать старую подписку
        old_sub = await self.sub_repo.get_active_subscription(user.id)
        if old_sub:
            old_sub.is_active = False
            # Если старая ещё не истекла — добавить остаток
            if old_sub.expires_at > now:
                remaining = old_sub.expires_at - now
                expires_at += remaining

        # Создать подписку
        sub = await self.sub_repo.create(
            user_id=user.id,
            plan_id=plan.id,
            starts_at=now,
            expires_at=expires_at,
            is_active=True,
        )

        # Создать платёж
        await self.pay_repo.create(
            user_id=user.id,
            plan_id=plan.id,
            subscription_id=sub.id,
            amount=float(plan.price_usd),
            currency="USD",
            payment_method=payment_method,
            provider_payment_id=provider_payment_id,
            status=PaymentStatus.COMPLETED,
        )

        logger.info(
            "Subscription activated: user=%d plan=%s until=%s",
            user.id, plan.slug, expires_at.isoformat()
        )
        return sub

    # ── Ручная монетизация: заявки ─────────────────────────
    async def get_pending_request(self, user_id: int) -> SubscriptionRequest | None:
        return await self.req_repo.get_pending_for_user(user_id)

    async def create_request(self, user: User, plan: Plan) -> SubscriptionRequest:
        """Создать заявку на подписку. Антидубль: одна pending на юзера."""
        existing = await self.req_repo.get_pending_for_user(user.id)
        if existing:
            raise DuplicateRequestError(existing)
        req = await self.req_repo.create(
            user_id=user.id,
            plan_id=plan.id,
            status=SubscriptionRequestStatus.PENDING,
        )
        logger.info("Subscription request created: id=%d user=%d plan=%s",
                    req.id, user.id, plan.slug)
        return req

    async def approve_request(
        self, request_id: int, owner_id: int
    ) -> tuple[str, SubscriptionRequest | None]:
        """Одобрить заявку. Идемпотентно.

        Возвращает (outcome, request), где outcome ∈
        {"approved", "already", "not_found", "not_pending"}.
        """
        req = await self.req_repo.get_by_id(request_id)
        if not req:
            return "not_found", None
        if req.status == SubscriptionRequestStatus.APPROVED:
            return "already", req
        if req.status != SubscriptionRequestStatus.PENDING:
            return "not_pending", req

        user = await self.session.get(User, req.user_id)
        plan = await self.session.get(Plan, req.plan_id)
        if not user or not plan:
            return "not_found", req

        sub = await self.activate_subscription(
            user=user, plan=plan, payment_method="manual",
            provider_payment_id=f"manual:req:{req.id}",
        )
        req.status = SubscriptionRequestStatus.APPROVED
        req.decided_by = owner_id
        req.decided_at = datetime.now(timezone.utc)
        req.subscription_id = sub.id
        await self.session.flush()
        logger.info("Request approved: id=%d user=%d plan=%s by_owner=%d",
                    req.id, user.id, plan.slug, owner_id)
        return "approved", req

    async def reject_request(
        self, request_id: int, owner_id: int, note: str | None = None
    ) -> tuple[str, SubscriptionRequest | None]:
        """Отклонить заявку. Идемпотентно."""
        req = await self.req_repo.get_by_id(request_id)
        if not req:
            return "not_found", None
        if req.status == SubscriptionRequestStatus.REJECTED:
            return "already", req
        if req.status != SubscriptionRequestStatus.PENDING:
            return "not_pending", req
        req.status = SubscriptionRequestStatus.REJECTED
        req.decided_by = owner_id
        req.decided_at = datetime.now(timezone.utc)
        req.note = note
        await self.session.flush()
        logger.info("Request rejected: id=%d user=%d by_owner=%d",
                    req.id, req.user_id, owner_id)
        return "rejected", req

    async def cancel_subscription(self, user_id: int) -> bool:
        sub = await self.sub_repo.get_active_subscription(user_id)
        if not sub:
            return False
        sub.is_active = False
        sub.cancelled_at = datetime.now(timezone.utc)
        await self.session.flush()
        logger.info("Subscription cancelled: user=%d sub=%d", user_id, sub.id)
        return True

    async def get_payment_history(
        self, user_id: int, offset: int = 0, limit: int = 10
    ) -> Sequence[Payment]:
        return await self.pay_repo.get_user_payments(user_id, offset, limit)

    async def deactivate_expired(self) -> int:
        """Вызывается шедулером."""
        count = await self.sub_repo.deactivate_expired()
        if count:
            logger.info("Deactivated %d expired subscriptions", count)
        return count

    # ── Промокоды ──────────────────────────────────────────

    async def activate_promo(
        self, user: User, code: str
    ) -> tuple[str, Subscription | None]:
        """Активировать промокод.

        Returns (outcome, subscription):
            "activated" — подписка создана
            "not_found" — код не найден / неактивен / истёк
            "exhausted" — лимит использований исчерпан
        """
        promo = await self.promo_repo.get_active_by_code(code)
        if not promo:
            return "not_found", None

        plan = await self.session.get(Plan, promo.plan_id)
        if not plan:
            return "not_found", None

        # Создаём подписку с кастомной длительностью из промокода
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=promo.duration_days)

        # Деактивировать старую подписку, если есть
        old_sub = await self.sub_repo.get_active_subscription(user.id)
        if old_sub:
            old_sub.is_active = False
            if old_sub.expires_at > now:
                remaining = old_sub.expires_at - now
                expires_at += remaining

        sub = await self.sub_repo.create(
            user_id=user.id,
            plan_id=plan.id,
            starts_at=now,
            expires_at=expires_at,
            is_active=True,
        )

        # Запись о платеже (бесплатный — промокод)
        await self.pay_repo.create(
            user_id=user.id,
            plan_id=plan.id,
            subscription_id=sub.id,
            amount=0,
            currency="USD",
            payment_method="promo",
            provider_payment_id=f"promo:{promo.code}",
            status=PaymentStatus.COMPLETED,
        )

        # Увеличить счётчик использований
        await self.promo_repo.increment_usage(promo)

        logger.info(
            "Promo activated: user=%d code=%s plan=%s days=%d until=%s",
            user.id, promo.code, plan.slug, promo.duration_days,
            expires_at.isoformat(),
        )
        return "activated", sub

    async def create_promo(
        self,
        code: str,
        plan_id: int,
        duration_days: int,
        max_uses: int,
        created_by: int,
    ) -> PromoCode:
        """Создать промокод (Owner)."""
        promo = await self.promo_repo.create(
            code=code.upper().strip(),
            plan_id=plan_id,
            duration_days=duration_days,
            max_uses=max_uses,
            created_by=created_by,
        )
        logger.info("Promo created: code=%s plan_id=%d days=%d by=%d",
                     promo.code, plan_id, duration_days, created_by)
        return promo

    async def list_promos(self, limit: int = 50) -> list:
        """Список активных промокодов."""
        return list(await self.promo_repo.list_active(limit))

    async def delete_promo(self, promo_id: int) -> bool:
        """Деактивировать промокод."""
        return await self.promo_repo.deactivate(promo_id)
