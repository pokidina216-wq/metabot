"""
Middleware anti-flood + rate limiting через Redis.

Audit-fix: для Message-события вызывался `event.answer(..., show_alert=...)`,
а параметр show_alert у Message.answer отсутствует — это TypeError при
каждом срабатывании rate-limit. Также сохранили graceful fallback: если
Redis недоступен, throttle не блокирует пользователей (open-the-gate).
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
    1. Anti-flood — 1 запрос в секунду.
    2. Rate limit — N запросов в минуту.
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
        except Exception as e:  # noqa: BLE001
            # Если Redis недоступен — открываем дверь, чтобы не валить весь UX.
            logger.warning("Throttle: redis unavailable (%s) — bypass", e)
            return await handler(event, data)

        # Anti-flood
        try:
            if not await cache.flood_check(user_id):
                logger.debug("Flood detected: user=%d", user_id)
                return  # Тихо игнорируем
        except Exception as e:  # noqa: BLE001
            logger.warning("Throttle: flood_check error (%s) — bypass", e)
            return await handler(event, data)

        # Rate limit
        settings = get_settings()
        try:
            allowed, _remaining = await cache.rate_limit_check(
                user_id,
                limit=settings.rate_limit_per_minute,
                window_seconds=60,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Throttle: rate_limit_check error (%s) — bypass", e)
            return await handler(event, data)

        if not allowed:
            if isinstance(event, CallbackQuery):
                # show_alert поддерживается только у CallbackQuery.answer
                await event.answer("⏳ Слишком много запросов. Подождите минуту.",
                                   show_alert=True)
            elif isinstance(event, Message):
                await event.answer("⏳ Слишком много запросов. Подождите минуту.")
            return

        return await handler(event, data)

    @staticmethod
    def _get_user_id(event: TelegramObject) -> int | None:
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            return event.from_user.id
        return None
