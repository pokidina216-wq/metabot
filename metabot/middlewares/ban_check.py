"""
Middleware проверки бана.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery


class BanCheckMiddleware(BaseMiddleware):
    """Блокирует забаненных пользователей."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        db_user = data.get("db_user")
        if db_user and db_user.is_banned:
            from html import escape as _esc
            reason = _esc(db_user.ban_reason or "Не указана")
            if isinstance(event, Message):
                await event.answer(
                    f"🚫 Ваш аккаунт заблокирован.\nПричина: {reason}"
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(f"🚫 Аккаунт заблокирован: {reason}", show_alert=True)
            return
        return await handler(event, data)
