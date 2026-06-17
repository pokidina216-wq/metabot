"""
/start — приветствие Vexis, обработка реферальной ссылки.
"""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.keyboards import main_menu_kb
from metabot.models.user import User
from metabot.services.referral_service import ReferralService
from metabot.configs import get_settings

logger = logging.getLogger(__name__)

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    db_user: User,
    is_new_user: bool,
) -> None:
    """Обработчик /start с поддержкой deep link (рефералы)."""
    settings = get_settings()

    # Обработка реферальной ссылки
    if is_new_user and command.args and command.args.startswith("ref_"):
        ref_code = command.args[4:]
        ref_service = ReferralService(session, settings.bot_username)
        success = await ref_service.process_referral(db_user, ref_code)
        if success:
            logger.info(
                "Referral link used: new_user=%d, code=%s",
                db_user.telegram_id, ref_code,
            )

    name = message.from_user.first_name

    if is_new_user:
        text = (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  🔮 <b>VEXIS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎉 Добро пожаловать, <b>{name}</b>!\n\n"
            f"Vexis — ваш персональный инструмент\n"
            f"для анализа данных и цифровой разведки.\n\n"
            f"🔹 <b>Проверка данных</b> — OSINT по номеру, почте, нику\n"
            f"🔹 <b>Метаданные</b> — EXIF, GPS, автор из любых файлов\n"
            f"🔹 <b>Username Finder</b> — поиск свободных @username\n\n"
            f"🆓 Ваш тариф: <b>Free</b> (5 запросов в день)\n"
            f"💎 Обновите до Premium для полного доступа!\n\n"
            f"Выберите действие ниже 👇"
        )
    else:
        text = (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  🔮 <b>VEXIS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👋 С возвращением, <b>{name}</b>!\n\n"
            f"Выберите действие ниже 👇"
        )

    await message.answer(text, reply_markup=main_menu_kb(), parse_mode="HTML")
