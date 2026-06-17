"""
Vexis — Telegram Info.
Поиск по @username и пересланным сообщениям.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Router, F
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.keyboards import tg_info_result_kb
from metabot.models.user import User
from metabot.repositories.request_log_repo import RequestLogRepository
from metabot.services.user_service import UserService

logger = logging.getLogger(__name__)

router = Router(name="telegram_info")


@router.message(F.forward_from)
async def handle_forwarded(
    message: Message, session: AsyncSession, db_user: User,
) -> None:
    """Информация об отправителе пересланного сообщения."""
    user_service = UserService(session)
    allowed, remaining = await user_service.check_and_increment(db_user)
    if not allowed:
        await message.answer("⚠️ Дневной лимит исчерпан! 💎 «Подписка»")
        return

    fwd = message.forward_from
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "  🆔 <b>TELEGRAM INFO</b>",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
        f"┣ 👤 <b>Имя:</b> {fwd.first_name or ''} {fwd.last_name or ''}",
        f"┣ 🆔 <b>ID:</b> <code>{fwd.id}</code>",
    ]
    if fwd.username:
        lines.append(f"┣ 📛 <b>Username:</b> @{fwd.username}")
    lines.append(f"┣ 🤖 <b>Бот:</b> {'Да' if fwd.is_bot else 'Нет'}")
    if fwd.is_premium:
        lines.append("┣ ⭐ <b>Premium:</b> Да")
    if fwd.language_code:
        lines.append(f"┣ 🌐 <b>Язык:</b> {fwd.language_code}")
    lines.append(f"┗ 📊 <b>Осталось:</b> {remaining}")

    log_repo = RequestLogRepository(session)
    await log_repo.create(
        user_id=db_user.id, action="telegram_info",
        details=f"forwarded from {fwd.id}",
    )

    await message.answer(
        "\n".join(lines), parse_mode="HTML", reply_markup=tg_info_result_kb(),
    )


@router.message(F.forward_sender_name)
async def handle_forwarded_hidden(message: Message) -> None:
    """Пересланное от пользователя со скрытым профилем."""
    name = message.forward_sender_name
    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🆔 <b>TELEGRAM INFO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"┣ 👤 <b>Имя:</b> {name}\n"
        f"┗ 🔒 <b>Статус:</b> Профиль скрыт\n\n"
        "<i>Пользователь скрыл пересылку\n"
        "в настройках приватности.</i>",
        parse_mode="HTML",
        reply_markup=tg_info_result_kb(),
    )


@router.message(F.text.regexp(r"^@[a-zA-Z][a-zA-Z0-9_]{3,31}$"))
async def handle_username_lookup(
    message: Message, bot: Bot, session: AsyncSession, db_user: User,
) -> None:
    """Поиск информации по @username."""
    user_service = UserService(session)
    allowed, remaining = await user_service.check_and_increment(db_user)
    if not allowed:
        await message.answer("⚠️ Дневной лимит исчерпан! 💎 «Подписка»")
        return

    username = message.text.strip()
    wait_msg = await message.answer(
        f"🔍 Ищу информацию о {username}...",
    )

    try:
        chat = await bot.get_chat(username)

        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━",
            "  🆔 <b>TELEGRAM INFO</b>",
            "━━━━━━━━━━━━━━━━━━━━━━\n",
        ]

        if chat.type == "private":
            lines.append("┣ 📌 <b>Тип:</b> Пользователь")
            lines.append(f"┣ 🆔 <b>ID:</b> <code>{chat.id}</code>")
            lines.append(f"┣ 📛 <b>Имя:</b> {chat.first_name or ''} {chat.last_name or ''}")
            if chat.username:
                lines.append(f"┣ 🔗 <b>Username:</b> @{chat.username}")
            if chat.bio:
                lines.append(f"┣ 📝 <b>Био:</b> {chat.bio}")
            if chat.has_private_forwards:
                lines.append("┣ 🔒 <b>Пересылка:</b> Запрещена")
        elif chat.type in ("group", "supergroup"):
            lines.append("┣ 📌 <b>Тип:</b> Группа")
            lines.append(f"┣ 🆔 <b>ID:</b> <code>{chat.id}</code>")
            lines.append(f"┣ 📛 <b>Название:</b> {chat.title}")
            if chat.description:
                lines.append(f"┣ 📝 <b>Описание:</b> {chat.description[:200]}")
            if chat.member_count:
                lines.append(f"┣ 👥 <b>Участников:</b> {chat.member_count:,}")
        elif chat.type == "channel":
            lines.append("┣ 📌 <b>Тип:</b> Канал")
            lines.append(f"┣ 🆔 <b>ID:</b> <code>{chat.id}</code>")
            lines.append(f"┣ 📛 <b>Название:</b> {chat.title}")
            if chat.description:
                lines.append(f"┣ 📝 <b>Описание:</b> {chat.description[:200]}")
            if chat.member_count:
                lines.append(f"┣ 👥 <b>Подписчиков:</b> {chat.member_count:,}")

        lines.append(f"┗ 📊 <b>Осталось:</b> {remaining}")

        log_repo = RequestLogRepository(session)
        await log_repo.create(
            user_id=db_user.id, action="telegram_info",
            details=f"username lookup: {username}",
        )

        await wait_msg.edit_text(
            "\n".join(lines), parse_mode="HTML",
            reply_markup=tg_info_result_kb(),
        )

    except Exception:
        await wait_msg.edit_text(
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "  ❌ <b>НЕ НАЙДЕНО</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Аккаунт {username} не существует\n"
            "или недоступен.",
            parse_mode="HTML",
            reply_markup=tg_info_result_kb(),
        )
