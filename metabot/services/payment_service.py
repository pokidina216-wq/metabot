"""
Сервис платежей — Telegram Stars + Telegram Payments.
"""
from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import LabeledPrice
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.configs import get_settings
from metabot.models.subscription import Plan
from metabot.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)


class PaymentService:
    """Генерация инвойсов и обработка платежей."""

    def __init__(self, session: AsyncSession, bot: Bot) -> None:
        self.session = session
        self.bot = bot
        self.sub_service = SubscriptionService(session)
        self.settings = get_settings()

    async def send_stars_invoice(
        self, chat_id: int, plan: Plan
    ) -> None:
        """Отправить инвойс для оплаты через Telegram Stars."""
        prices = [LabeledPrice(label=plan.name, amount=plan.price_stars)]
        await self.bot.send_invoice(
            chat_id=chat_id,
            title=f"🌟 {plan.name}",
            description=f"Подписка {plan.name} на {plan.duration_days} дней",
            payload=f"stars:{plan.slug}",
            currency="XTR",  # Telegram Stars
            prices=prices,
        )

    async def send_provider_invoice(
        self, chat_id: int, plan: Plan
    ) -> None:
        """Отправить инвойс через Telegram Payments (Stripe и др.)."""
        if not self.settings.payments_provider_token:
            logger.warning("Payments provider token not configured")
            return

        prices = [
            LabeledPrice(
                label=plan.name,
                amount=int(plan.price_usd * 100),  # В центах
            )
        ]
        await self.bot.send_invoice(
            chat_id=chat_id,
            title=f"💎 {plan.name}",
            description=f"Подписка {plan.name} на {plan.duration_days} дней",
            payload=f"provider:{plan.slug}",
            provider_token=self.settings.payments_provider_token,
            currency="USD",
            prices=prices,
        )

    async def process_successful_payment(
        self,
        user_id: int,
        telegram_id: int,
        payload: str,
        provider_payment_id: str | None = None,
    ) -> bool:
        """Обработка успешного платежа."""
        try:
            method, plan_slug = payload.split(":", 1)
            plan = await self.sub_service.get_plan_by_slug(plan_slug)
            if not plan:
                logger.error("Plan not found: %s", plan_slug)
                return False

            from metabot.services.user_service import UserService
            user_service = UserService(self.session)
            user = await user_service.get_user(telegram_id)
            if not user:
                logger.error("User not found: tg_id=%d", telegram_id)
                return False

            await self.sub_service.activate_subscription(
                user=user,
                plan=plan,
                payment_method=method,
                provider_payment_id=provider_payment_id,
            )
            logger.info(
                "Payment processed: user=%d plan=%s method=%s",
                user.id, plan_slug, method
            )
            return True

        except Exception as e:
            logger.exception("Payment processing error: %s", e)
            return False
