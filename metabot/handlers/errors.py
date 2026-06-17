"""
Vexis — Глобальный обработчик ошибок.
Безопасный: не раскрывает внутренние детали пользователю.
"""
from __future__ import annotations

import logging
import traceback

from aiogram import Router
from aiogram.types import ErrorEvent, Update

logger = logging.getLogger(__name__)

router = Router(name="errors")


@router.errors()
async def global_error_handler(event: ErrorEvent) -> bool:
    """Глобальный перехват — логируем, уведомляем пользователя."""
    exception = event.exception
    update: Update = event.update

    logger.error(
        "Unhandled exception: %s\n%s",
        exception, traceback.format_exc(),
    )

    try:
        if update.message:
            await update.message.answer(
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "  ❌ <b>ОШИБКА</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Произошла внутренняя ошибка.\n"
                "Попробуйте позже или обратитесь\n"
                "в поддержку.",
                parse_mode="HTML",
            )
        elif update.callback_query:
            await update.callback_query.answer(
                "❌ Ошибка. Попробуйте позже.",
                show_alert=True,
            )
    except Exception as e:
        logger.error("Failed to notify user about error: %s", e)

    return True
