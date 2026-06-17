"""
/start — приветствие Vexis с графическим баннером.

Новый пользователь: фото + полное описание + меню.
Возвращающийся: фото + короткое приветствие + меню.
Реферальная ссылка обрабатывается автоматически.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Router
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import Message, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.keyboards import main_menu_kb
from metabot.models.user import User
from metabot.services.referral_service import ReferralService
from metabot.configs import get_settings
from metabot.utils.validators import sanitize

logger = logging.getLogger(__name__)

router = Router(name="start")

# Путь к баннеру приветствия (рядом с корнем проекта)
_WELCOME_IMG = Path(__file__).resolve().parents[2] / "assets" / "welcome.png"


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    db_user: User,
    is_new_user: bool,
) -> None:
    """Обработчик /start с баннером и deep link (рефералы)."""
    settings = get_settings()

    # ── Реферальная ссылка ───────────────────────────────
    if is_new_user and command.args and command.args.startswith("ref_"):
        ref_code = command.args[4:]
        ref_service = ReferralService(session, settings.bot_username)
        success = await ref_service.process_referral(db_user, ref_code)
        if success:
            logger.info(
                "Referral link used: new_user=%d, code=%s",
                db_user.telegram_id, ref_code,
            )

    name = sanitize(message.from_user.first_name or "", max_len=64) or "друг"

    # ── Текст ────────────────────────────────────────────
    if is_new_user:
        caption = (
            f"👋 <b>Добро пожаловать, {name}!</b>\n\n"
            "Vexis — ваш многофункциональный помощник\n"
            "для анализа данных, поиска информации\n"
            "и защиты вашей приватности.\n\n"
            "🔎 <b>Анализируй</b> — данные, файлы, метаданные\n"
            "🎯 <b>Находи</b> — людей, email, номера, домены\n"
            "🛡 <b>Защищай</b> — свою информацию и приватность\n\n"
            "🆓 Тариф: <b>Free</b> — 5 запросов в день\n"
            "💎 Откройте все возможности с <b>Premium!</b>\n\n"
            "Выберите действие ниже 👇"
        )
    else:
        caption = (
            f"👋 <b>С возвращением, {name}!</b>\n\n"
            "Выберите действие ниже 👇"
        )

    # ── Отправка с фото ──────────────────────────────────
    if _WELCOME_IMG.exists():
        try:
            photo = FSInputFile(_WELCOME_IMG)
            await message.answer_photo(
                photo=photo,
                caption=caption,
                reply_markup=main_menu_kb(),
                parse_mode="HTML",
            )
            return
        except Exception as e:
            logger.warning("Failed to send welcome photo: %s", e)

    # Fallback: без фото
    await message.answer(caption, reply_markup=main_menu_kb(), parse_mode="HTML")
