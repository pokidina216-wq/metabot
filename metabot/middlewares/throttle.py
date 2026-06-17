"""
Middleware anti-flood + rate limiting через Redis.

Стоит ПЕРВЫМ в цепочке (до Database/Auth) чтобы спам не создавал
лишнюю нагрузку на БД. При недоступности Redis — пропускаем (fail-open),
т.к. лучше пропустить запрос, чем сломать бота.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from metabot.cache.redis_cache import RedisCache, get_redis
from metabot.configs import get_settings

logger = logging.getLogger(__name__)


class ThrottleMiddleware(BaseMiddleware):
    """
    Двухуровневая защита:
    1. Anti-flood — 1 запрос в секунду
    2. Rate limit — N запросов в минуту
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user_id = self._get_user_id(event)
        if not user_id:
            return await handler(event, data)

        try:
            redis = await get_redis()
            cache = RedisCache(redis)

            # Anti-flood
            if not await cache.flood_check(user_id):
                logger.debug("Flood detected: user=%d", user_id)
                return  # Тихо игнорируем

            # Rate limit
            settings = get_settings()
            allowed, remaining = await cache.rate_limit_check(
                user_id,
                limit=settings.rate_limit_per_minute,
                window_seconds=60,
            )
            if not allowed:
                if isinstance(event, Message):
                    await event.answer("⏳ Слишком много запросов. Подождите минуту.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⏳ Подождите минуту.", show_alert=True)
                return
        except Exception as exc:
            # Redis недоступен — fail-open, пропускаем запрос
            logger.warning("Throttle middleware Redis error (fail-open): %s", exc)

        return await handler(event, data)

    @staticmethod
    def _get_user_id(event: TelegramObject) -> int | None:
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            return event.from_user.id
        return None
