"""
Middleware аутентификации — регистрирует пользователя при первом обращении.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from metabot.services.user_service import UserService


class AuthMiddleware(BaseMiddleware):
    """Гарантирует, что user существует в БД."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        session = data.get("session")
        if not session:
            return await handler(event, data)

        # Получаем Telegram-пользователя
        tg_user = None
        if isinstance(event, Message) and event.from_user:
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery) and event.from_user:
            tg_user = event.from_user

        if tg_user and not tg_user.is_bot:
            user_service = UserService(session)
            user, created = await user_service.register_or_update(
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                language_code=tg_user.language_code,
            )
            data["db_user"] = user
            data["is_new_user"] = created

            # Защита Owner: как только владелец появляется в БД — помечаем
            # его protected и роль OWNER (идемпотентно). Так защита включается
            # и при первом /start владельца, а не только при рестарте.
            from metabot.configs import get_settings
            owner_id = get_settings().owner_id
            if owner_id and tg_user.id == owner_id and (
                not user.is_protected or user.role.value != "owner" or user.is_banned
            ):
                from metabot.services.security_service import SecurityAuditService
                await SecurityAuditService(session).ensure_owner_protected(owner_id)
                await session.commit()

        return await handler(event, data)
