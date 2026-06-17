"""
APScheduler — фоновые задачи.
- Деактивация просроченных подписок (каждые 5 минут)
- Агрегация дневной аналитики (каждый день в 00:05)
- Очистка кэша (каждые 30 минут)
- Сброс дневных лимитов (каждый день в 00:00)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from metabot.cache.redis_cache import get_redis, RedisCache
from metabot.database import async_session_factory

logger = logging.getLogger(__name__)


async def deactivate_expired_subscriptions() -> None:
    """Деактивировать просроченные подписки."""
    try:
        from metabot.services.subscription_service import SubscriptionService
        async with async_session_factory() as session:
            service = SubscriptionService(session)
            count = await service.deactivate_expired()
            if count:
                logger.info("Deactivated %d expired subscriptions", count)
            await session.commit()
    except Exception as e:
        logger.exception("Scheduler: deactivate_expired error: %s", e)


async def aggregate_daily_analytics() -> None:
    """Агрегировать аналитику за день."""
    try:
        from metabot.services.analytics_service import AnalyticsService
        async with async_session_factory() as session:
            service = AnalyticsService(session)
            await service.aggregate_daily()
            await session.commit()
            logger.info("Daily analytics aggregated")
    except Exception as e:
        logger.exception("Scheduler: analytics aggregation error: %s", e)


async def cleanup_cache() -> None:
    """Очистить устаревшие записи в Redis."""
    try:
        redis = await get_redis()
        cache = RedisCache(redis)
        deleted = await cache.cleanup_expired()
        if deleted:
            logger.info("Cache cleanup: %d entries removed", deleted)
    except Exception as e:
        logger.exception("Scheduler: cache cleanup error: %s", e)


async def reset_daily_limits() -> None:
    """Сбросить дневные лимиты пользователей."""
    try:
        from metabot.repositories.user_repo import UserRepository
        async with async_session_factory() as session:
            repo = UserRepository(session)
            count = await repo.reset_daily_requests()
            await session.commit()
            logger.info("Reset daily limits for %d users", count)
    except Exception as e:
        logger.exception("Scheduler: reset_daily_limits error: %s", e)


def setup_scheduler() -> AsyncIOScheduler:
    """Настроить и вернуть планировщик."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Деактивация подписок — каждые 5 минут
    scheduler.add_job(
        deactivate_expired_subscriptions,
        IntervalTrigger(minutes=5),
        id="deactivate_subs",
        replace_existing=True,
    )

    # Агрегация аналитики — каждый день в 00:05 UTC
    scheduler.add_job(
        aggregate_daily_analytics,
        CronTrigger(hour=0, minute=5),
        id="daily_analytics",
        replace_existing=True,
    )

    # Очистка кэша — каждые 30 минут
    scheduler.add_job(
        cleanup_cache,
        IntervalTrigger(minutes=30),
        id="cache_cleanup",
        replace_existing=True,
    )

    # Сброс дневных лимитов — каждый день в 00:00 UTC
    scheduler.add_job(
        reset_daily_limits,
        CronTrigger(hour=0, minute=0),
        id="reset_daily",
        replace_existing=True,
    )

    return scheduler
