"""
Health-check сервер.

Лёгкий aiohttp-эндпоинт для liveness/readiness проб (Docker healthcheck,
Railway, k8s). В режиме polling запускается фоном на отдельном внутреннем
порту; в режиме webhook health-роуты подключаются к основному приложению.

Эндпоинты:
    GET /health  — liveness: процесс жив (всегда 200, если event loop крутится).
    GET /ready   — readiness: проверяет доступность Postgres и Redis.
                   200 если всё ОК, иначе 503 с деталями по компонентам.
"""
from __future__ import annotations

import logging

from aiohttp import web
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def _check_postgres() -> tuple[bool, str]:
    try:
        from metabot.database import async_session_factory

        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


async def _check_redis() -> tuple[bool, str]:
    try:
        from metabot.cache.redis_cache import get_redis

        redis = await get_redis()
        await redis.ping()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


async def handle_health(_: web.Request) -> web.Response:
    """Liveness: процесс отвечает — значит жив."""
    return web.json_response({"status": "alive"})


async def handle_ready(_: web.Request) -> web.Response:
    """Readiness: зависимости (Postgres, Redis) доступны."""
    pg_ok, pg_detail = await _check_postgres()
    redis_ok, redis_detail = await _check_redis()
    ready = pg_ok and redis_ok
    payload = {
        "status": "ready" if ready else "degraded",
        "checks": {
            "postgres": {"ok": pg_ok, "detail": pg_detail},
            "redis": {"ok": redis_ok, "detail": redis_detail},
        },
    }
    return web.json_response(payload, status=200 if ready else 503)


def attach_health_routes(app: web.Application) -> None:
    """Подключить health-роуты к существующему aiohttp-приложению (webhook)."""
    app.router.add_get("/health", handle_health)
    app.router.add_get("/ready", handle_ready)


async def start_health_server(host: str, port: int) -> web.AppRunner:
    """Запустить отдельный health-сервер (polling-режим). Возвращает runner."""
    app = web.Application()
    attach_health_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("Health server started on %s:%d (/health, /ready)", host, port)
    return runner
