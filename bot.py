"""
Vexis — Главная точка входа.

Запуск:
    python bot.py          # Polling
    python bot.py --webhook # Webhook
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)
from aiohttp import web

from metabot.configs import get_settings
from metabot.cache.redis_cache import get_redis
from metabot.database import async_engine, async_session_factory
from metabot.handlers import setup_routers
from metabot.middlewares import (
    DatabaseMiddleware,
    AuthMiddleware,
    ThrottleMiddleware,
    LoggingMiddleware,
    BanCheckMiddleware,
    RBACMiddleware,
)
from metabot.scheduler import setup_scheduler
from metabot.health import attach_health_routes, start_health_server
from metabot.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


async def on_startup(bot: Bot) -> None:
    """Действия при запуске бота."""
    settings = get_settings()

    # Запускаем seed если нужно
    await seed_default_plans()

    # Защита Owner: помечаем запись владельца protected (если он уже в БД)
    await ensure_owner_protected_on_start()

    # Настраиваем webhook если нужно
    if settings.webhook_url:
        webhook_url = f"{settings.webhook_url}{settings.webhook_path}"
        await bot.set_webhook(
            url=webhook_url,
            secret_token=settings.webhook_secret,
            drop_pending_updates=True,
        )
        logger.info("Webhook set: %s", webhook_url)
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook removed, polling mode")

    logger.info("🔮 Vexis started!")


async def on_shutdown(bot: Bot) -> None:
    """Действия при остановке."""
    await bot.session.close()
    logger.info("🔮 Vexis stopped")


async def ensure_owner_protected_on_start() -> None:
    """Помечает запись владельца как protected при старте (идемпотентно)."""
    from metabot.services.security_service import SecurityAuditService

    settings = get_settings()
    if not settings.owner_id:
        logger.warning("OWNER_ID не задан — защита Owner не активирована")
        return
    async with async_session_factory() as session:
        svc = SecurityAuditService(session)
        user = await svc.ensure_owner_protected(settings.owner_id)
        await session.commit()
        if user:
            logger.info("Owner protection ensured for tg_id=%d", settings.owner_id)
        else:
            logger.info(
                "Owner (tg_id=%d) ещё не в БД — защита включится при первом /start",
                settings.owner_id,
            )


async def seed_default_plans() -> None:
    """Создать тарифы по умолчанию если их нет."""
    from metabot.repositories.subscription_repo import PlanRepository
    from metabot.models.subscription import Plan

    async with async_session_factory() as session:
        repo = PlanRepository(session)
        existing = await repo.get_active_plans()
        if existing:
            return

        plans = [
            Plan(
                name="Free",
                slug="free",
                price_usd=0,
                price_stars=0,
                duration_days=36500,  # "вечный"
                daily_request_limit=5,
                osint_enabled=False,
                is_active=True,
                sort_order=0,
            ),
            Plan(
                name="Premium",
                slug="premium",
                price_usd=3.00,
                price_stars=150,
                duration_days=30,
                daily_request_limit=50,
                osint_enabled=True,
                is_active=True,
                sort_order=1,
            ),
            Plan(
                name="VIP",
                slug="vip",
                price_usd=7.00,
                price_stars=350,
                duration_days=90,
                daily_request_limit=200,
                osint_enabled=True,
                is_active=True,
                sort_order=2,
            ),
        ]
        for plan in plans:
            session.add(plan)
        await session.commit()
        logger.info("Default plans seeded: Free, Premium, VIP")


def create_dispatcher() -> Dispatcher:
    """Создать и настроить Dispatcher."""
    settings = get_settings()

    # FSM storage в Redis
    storage = RedisStorage.from_url(settings.redis_dsn)
    dp = Dispatcher(storage=storage)

    # ─── Порядок middleware (снаружи → внутрь) ───────────────
    # 1. Logging — замер времени, не требует БД
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())

    # 2. Throttle — отсекаем спам ДО обращения к БД (экономия ресурсов)
    dp.message.middleware(ThrottleMiddleware())
    dp.callback_query.middleware(ThrottleMiddleware())

    # 3. Database — сессия для хендлеров
    dp.message.middleware(DatabaseMiddleware())
    dp.callback_query.middleware(DatabaseMiddleware())

    # 4. Auth — регистрация/обновление пользователя в БД
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())

    # 5. BanCheck — блокировка забаненных (после Auth, т.к. нужен db_user)
    dp.message.middleware(BanCheckMiddleware())
    dp.callback_query.middleware(BanCheckMiddleware())

    # 6. RBAC — вычисление effective_role, is_owner (после Auth)
    dp.message.middleware(RBACMiddleware())
    dp.callback_query.middleware(RBACMiddleware())

    # Роутеры
    root_router = setup_routers()
    dp.include_router(root_router)

    # Lifecycle
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    return dp


async def run_polling() -> None:
    """Запуск в режиме Long Polling."""
    settings = get_settings()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher()

    # Scheduler
    scheduler = setup_scheduler()
    scheduler.start()

    # Health-сервер (внутренний порт, наружу не публикуется)
    health_runner = None
    if settings.health_check_enabled:
        health_runner = await start_health_server(
            host="0.0.0.0", port=settings.health_check_port
        )

    try:
        logger.info("Starting polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        if health_runner is not None:
            await health_runner.cleanup()
        await bot.session.close()


async def run_webhook() -> None:
    """Запуск в режиме Webhook."""
    settings = get_settings()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher()

    # Scheduler
    scheduler = setup_scheduler()
    scheduler.start()

    # Web server
    app = web.Application()
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret,
    )
    webhook_handler.register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)

    # Health-роуты на том же web-приложении
    attach_health_routes(app)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.web_server_port)
    logger.info("Starting webhook on port %d...", settings.web_server_port)

    try:
        await site.start()
        await asyncio.Event().wait()  # Блокируем навсегда
    finally:
        scheduler.shutdown()
        await runner.cleanup()
        await bot.session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Vexis Bot")
    parser.add_argument("--webhook", action="store_true", help="Run in webhook mode")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(level=settings.log_level, log_format=settings.log_format)

    if args.webhook:
        asyncio.run(run_webhook())
    else:
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
