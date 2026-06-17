"""
RBAC middleware — прокидывает в хендлеры эффективную роль и флаг владельца.

После AuthMiddleware в data есть db_user. Здесь вычисляем:
- data["effective_role"]: UserRole с учётом Owner (по telegram_id)
- data["is_owner"]: bool
Так хендлеры и фильтры могут проверять права единообразно.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from metabot.configs import get_settings
from metabot.models.user import UserRole
from metabot.security.roles import effective_role, is_owner


class RBACMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        settings = get_settings()
        owner_id = settings.owner_id
        db_user = data.get("db_user")
        tg_id = getattr(getattr(event, "from_user", None), "id", None)
        db_role = getattr(db_user, "role", UserRole.USER) if db_user else UserRole.USER

        data["effective_role"] = effective_role(db_role, tg_id, owner_id)
        data["is_owner"] = is_owner(tg_id, owner_id)
        return await handler(event, data)
