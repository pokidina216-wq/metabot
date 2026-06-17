"""
Vexis — Подписки: отмена, история, навигация.
Тарифы и покупка обрабатываются в payment_handler.py.
"""
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.keyboards import (
    subscription_kb,
    confirm_kb,
    nav_home_kb,
)
from metabot.models.user import User
from metabot.services.subscription_service import SubscriptionService

router = Router(name="subscription")


@router.callback_query(F.data == "sub:cancel")
async def cb_cancel_subscription(
    callback: CallbackQuery, session: AsyncSession, db_user: User,
) -> None:
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  ⚠️ <b>ОТМЕНА ПОДПИСКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Вы уверены, что хотите отменить подписку?\n"
        "Ваш тариф станет <b>Free</b>.",
        reply_markup=confirm_kb("cancel_sub"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "confirm:cancel_sub")
async def cb_confirm_cancel(
    callback: CallbackQuery, session: AsyncSession, db_user: User,
) -> None:
    sub_service = SubscriptionService(session)
    success = await sub_service.cancel_subscription(db_user.id)

    if success:
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  ✅ <b>ПОДПИСКА ОТМЕНЕНА</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Ваш тариф: <b>Free</b>\n"
            "5 запросов в день."
        )
    else:
        text = "⚠️ Активная подписка не найдена."

    await callback.message.edit_text(
        text, reply_markup=subscription_kb(has_active=False), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "sub:history")
async def cb_payment_history(
    callback: CallbackQuery, session: AsyncSession, db_user: User,
) -> None:
    sub_service = SubscriptionService(session)
    payments = await sub_service.get_payment_history(db_user.id)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "  📜 <b>ИСТОРИЯ ПЛАТЕЖЕЙ</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    if not payments:
        lines.append("  Платежей пока нет.")
    else:
        for p in payments:
            dt = p.created_at.strftime("%d.%m.%Y %H:%M")
            icons = {"completed": "✅", "pending": "⏳", "failed": "❌", "refunded": "↩️"}
            icon = icons.get(p.status.value, "❓")
            method = {"promo": "🎟 промо", "manual": "✋ вручную", "stars": "⭐"}.get(
                p.payment_method, p.payment_method
            )
            lines.append(f"  {dt} — {icon} ${p.amount} ({method})")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=subscription_kb(has_active=True),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "sub:back")
async def cb_sub_back(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "◈ <b>ПОДПИСКА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите действие:",
        reply_markup=subscription_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("❌ Отменено.", reply_markup=nav_home_kb())
    await callback.answer()
