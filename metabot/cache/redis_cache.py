"""
Redis-кэш — горячий слой для rate-limiting, сессий и быстрого кэша.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from metabot.configs import get_settings

logger = logging.getLogger(__name__)

_redis_pool: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    """Получить (или создать) пул подключений к Redis."""
    global _redis_pool
    if _redis_pool is None:
        settings = get_settings()
        _redis_pool = aioredis.from_url(
            settings.redis_dsn,
            decode_responses=True,
            max_connections=50,
        )
        logger.info("Redis pool created: %s", settings.redis_dsn)
    return _redis_pool


async def close_redis() -> None:
    global _redis_pool
    if _redis_pool:
        await _redis_pool.close()
        _redis_pool = None


class RedisCache:
    """Высокоуровневая обёртка над Redis."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self.r = redis

    # ── Key-Value ──────────────────────────────────────────
    async def get(self, key: str) -> Optional[str]:
        return await self.r.get(key)

    async def get_json(self, key: str) -> Optional[Any]:
        raw = await self.r.get(key)
        if raw:
            return json.loads(raw)
        return None

    async def set(self, key: str, value: str, ttl: int = 3600) -> None:
        await self.r.set(key, value, ex=ttl)

    async def set_json(self, key: str, data: Any, ttl: int = 3600) -> None:
        await self.r.set(key, json.dumps(data, ensure_ascii=False, default=str), ex=ttl)

    async def delete(self, key: str) -> None:
        await self.r.delete(key)

    # ── Rate Limiting (sliding window) ─────────────────────
    async def rate_limit_check(
        self, user_id: int, limit: int, window_seconds: int = 60
    ) -> tuple[bool, int]:
        """
        Возвращает (allowed, remaining).
        Используем sorted set со скользящим окном.
        """
        import time

        key = f"ratelimit:{user_id}"
        now = time.time()
        window_start = now - window_seconds

        pipe = self.r.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, window_seconds + 1)
        results = await pipe.execute()

        current_count = results[1]
        remaining = max(0, limit - current_count - 1)
        allowed = current_count < limit

        if not allowed:
            # Откатываем добавление
            await self.r.zrem(key, str(now))

        return allowed, remaining

    # ── Anti-Flood ─────────────────────────────────────────
    async def flood_check(self, user_id: int, cooldown_seconds: int = 1) -> bool:
        """True если запрос разрешён (нет флуда)."""
        key = f"flood:{user_id}"
        exists = await self.r.exists(key)
        if exists:
            return False
        await self.r.set(key, "1", ex=cooldown_seconds)
        return True

    # ── User Session / State ───────────────────────────────
    async def set_user_state(self, user_id: int, state: str, data: dict | None = None) -> None:
        key = f"state:{user_id}"
        payload = {"state": state, "data": data or {}}
        await self.set_json(key, payload, ttl=3600)

    async def get_user_state(self, user_id: int) -> Optional[dict]:
        return await self.get_json(f"state:{user_id}")

    async def clear_user_state(self, user_id: int) -> None:
        await self.delete(f"state:{user_id}")

    # ── Cleanup ────────────────────────────────────────────
    async def cleanup_expired(self) -> int:
        """Удалить устаревшие ключи rate-limit / flood."""
        import time

        deleted = 0
        cursor = "0"
        while True:
            cursor, keys = await self.r.scan(cursor=cursor, match="ratelimit:*", count=100)
            for key in keys:
                removed = await self.r.zremrangebyscore(key, 0, time.time() - 120)
                deleted += removed
                # Удаляем пустые ключи
                if await self.r.zcard(key) == 0:
                    await self.r.delete(key)
            if cursor == "0" or cursor == 0:
                break
        return deleted
