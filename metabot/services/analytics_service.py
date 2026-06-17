"""
Сервис аналитики — сбор и отображение метрик.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from metabot.repositories.analytics_repo import AnalyticsRepository
from metabot.repositories.user_repo import UserRepository
from metabot.repositories.request_log_repo import RequestLogRepository
from metabot.repositories.payment_repo import PaymentRepository

logger = logging.getLogger(__name__)


@dataclass
class DashboardStats:
    """Данные для панели аналитики."""
    total_users: int
    new_users_today: int
    new_users_week: int
    active_today: int
    active_week: int
    requests_today: int
    requests_week: int
    revenue_today: float
    revenue_week: float
    popular_tools: list


class AnalyticsService:
    """Сбор метрик для админ-панели и шедулера."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.analytics_repo = AnalyticsRepository(session)
        self.user_repo = UserRepository(session)
        self.log_repo = RequestLogRepository(session)
        self.pay_repo = PaymentRepository(session)

    async def get_dashboard(self) -> DashboardStats:
        """Собрать данные для дашборда."""
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = now - timedelta(days=7)

        total_users = await self.user_repo.count()
        new_today = await self.user_repo.count_new_since(today_start)
        new_week = await self.user_repo.count_new_since(week_ago)
        active_today = await self.user_repo.count_active_since(today_start)
        active_week = await self.user_repo.count_active_since(week_ago)
        requests_today = await self.log_repo.count_total_since(today_start)
        requests_week = await self.log_repo.count_total_since(week_ago)
        revenue_today = await self.pay_repo.total_revenue_since(today_start)
        revenue_week = await self.pay_repo.total_revenue_since(week_ago)
        popular = await self.log_repo.popular_actions(week_ago, limit=5)

        return DashboardStats(
            total_users=total_users,
            new_users_today=new_today,
            new_users_week=new_week,
            active_today=active_today,
            active_week=active_week,
            requests_today=requests_today,
            requests_week=requests_week,
            revenue_today=revenue_today,
            revenue_week=revenue_week,
            popular_tools=[(row[0], row[1]) for row in popular],
        )

    async def aggregate_daily(self, target_date: date | None = None) -> None:
        """Агрегировать метрики за день (вызывается шедулером)."""
        if target_date is None:
            target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

        day_start = datetime.combine(target_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        day_end = day_start + timedelta(days=1)

        new_users = await self.user_repo.count_new_since(day_start)
        active = await self.user_repo.count_active_since(day_start)
        requests = await self.log_repo.count_total_since(day_start)
        revenue = await self.pay_repo.total_revenue_since(day_start)

        await self.analytics_repo.upsert_metric(target_date, "new_users", new_users)
        await self.analytics_repo.upsert_metric(target_date, "active_users", active)
        await self.analytics_repo.upsert_metric(target_date, "total_requests", requests)
        await self.analytics_repo.upsert_metric(
            target_date, "revenue_usd", 0, value_decimal=revenue
        )

        logger.info("Analytics aggregated for %s", target_date)
