"""
Middleware логирования — записывает каждый запрос.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

logger = logging.getLogger("metabot.requests")


class LoggingMiddleware(BaseMiddleware):
    """Логирует все входящие events."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        start = time.monotonic()

        user_id = None
        event_type = type(event).__name__

        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
            event_type = f"Message:{event.content_type}"
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id
            event_type = f"Callback:{event.data}"

        try:
            result = await handler(event, data)
            elapsed = (time.monotonic() - start) * 1000
            logger.info(
                "user=%s type=%s time=%.0fms",
                user_id, event_type, elapsed
            )
            return result
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            logger.error(
                "user=%s type=%s time=%.0fms error=%s",
                user_id, event_type, elapsed, e
            )
            raise
