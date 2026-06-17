"""
Middleware anti-flood + rate limiting через Redis.
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
                await event.answer(
                    "⏳ Слишком много запросов. Подождите минуту.",
                    show_alert=True if isinstance(event, CallbackQuery) else False,
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("⏳ Подождите минуту.", show_alert=True)
            return

        return await handler(event, data)

    @staticmethod
    def _get_user_id(event: TelegramObject) -> int | None:
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            return event.from_user.id
        return None
