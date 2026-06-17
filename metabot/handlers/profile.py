"""
Vexis — Профиль: история запросов, настройки.
"""
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.keyboards import profile_kb, pagination_kb
from metabot.models.user import User
from metabot.repositories.request_log_repo import RequestLogRepository

router = Router(name="profile")


@router.callback_query(F.data == "profile:history")
async def cb_request_history(
    callback: CallbackQuery, session: AsyncSession, db_user: User,
) -> None:
    """История запросов пользователя."""
    log_repo = RequestLogRepository(session)
    logs = await log_repo.get_many(
        user_id=db_user.id,
        offset=0,
        limit=15,
        order_by=RequestLogRepository.model.created_at.desc(),
    )

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "  📊 <b>ИСТОРИЯ ЗАПРОСОВ</b>",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    if not logs:
        lines.append("Запросов пока нет.\n")
        lines.append("Попробуйте «🔍 Проверка данных»\n"
                      "или «📂 Метаданные»!")
    else:
        action_labels = {
            "metadata_analysis": "📂 Метаданные",
            "username_search": "🔤 Username",
            "username_check": "🔍 Проверка UN",
            "osint_query": "🔍 OSINT",
            "telegram_info": "🆔 TG Info",
        }
        for log in logs:
            dt = log.created_at.strftime("%d.%m %H:%M")
            label = action_labels.get(log.action, log.action)
            time_str = f" ({log.processing_time_ms}мс)" if log.processing_time_ms else ""
            lines.append(f"  {dt} — {label}{time_str}")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=profile_kb(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "profile:settings")
async def cb_settings(callback: CallbackQuery, db_user: User) -> None:
    """Переход к настройкам из профиля."""
    from metabot.keyboards import settings_kb
    notif = db_user.notifications_enabled
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  ⚙️ <b>НАСТРОЙКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔔 Уведомления: {'✅ Включены' if notif else '❌ Выключены'}\n"
        f"🌐 Язык: {db_user.language_code or 'Авто'}"
    )
    await callback.message.edit_text(
        text, reply_markup=settings_kb(notif), parse_mode="HTML",
    )
    await callback.answer()
