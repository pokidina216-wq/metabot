"""
Сервис пользователей — бизнес-логика поверх репозитория.
"""
from __future__ import annotations

import secrets
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from metabot.models.user import User, UserRole
from metabot.repositories.user_repo import UserRepository
from metabot.repositories.subscription_repo import SubscriptionRepository

logger = logging.getLogger(__name__)


class UserService:
    """Бизнес-логика для пользователей."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = UserRepository(session)
        self.sub_repo = SubscriptionRepository(session)

    async def register_or_update(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language_code: str | None = None,
    ) -> tuple[User, bool]:
        """Регистрация или обновление пользователя."""
        referral_code = self._generate_referral_code()
        user, created = await self.repo.get_or_create(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
            referral_code=referral_code,
        )
        if created:
            logger.info("New user registered: tg_id=%d", telegram_id)
        return user, created

    async def get_user(self, telegram_id: int) -> User | None:
        return await self.repo.get_by_telegram_id(telegram_id)

    async def get_daily_limit(self, user: User) -> int:
        """Возвращает дневной лимит запросов с учётом подписки."""
        sub = await self.sub_repo.get_active_subscription(user.id)
        if sub and sub.plan:
            return sub.plan.daily_request_limit
        # Free-лимит
        return 5

    async def check_and_increment(self, user: User) -> tuple[bool, int]:
        """
        Атомарно проверить дневной лимит и списать 1 запрос.
        Возвращает (allowed, remaining).

        Атомарность на уровне БД (условный UPDATE) исключает гонку, когда два
        одновременных запроса оба проходят проверку до инкремента.
        """
        limit = await self.get_daily_limit(user)
        allowed, remaining = await self.repo.try_consume_daily(user.id, limit)
        return allowed, remaining

    async def get_user_plan_name(self, user: User) -> str:
        """Название текущего тарифа."""
        sub = await self.sub_repo.get_active_subscription(user.id)
        if sub and sub.plan:
            return sub.plan.name
        return "Free"

    async def update_notifications(self, user: User, enabled: bool) -> None:
        """Включить/выключить уведомления."""
        user.notifications_enabled = enabled
        await self.session.flush()

    async def update_language(self, user: User, lang: str) -> None:
        """Обновить язык пользователя."""
        user.language_code = lang
        await self.session.flush()

    @staticmethod
    def _generate_referral_code() -> str:
        return secrets.token_urlsafe(8)
