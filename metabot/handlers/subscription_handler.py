"""
Vexis — Подписки: просмотр тарифов, покупка, отмена.
"""
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.keyboards import (
    plan_selection_kb,
    request_subscription_kb,
    subscription_kb,
    confirm_kb,
    nav_home_kb,
)
from metabot.models.user import User
from metabot.services.subscription_service import SubscriptionService

router = Router(name="subscription")


@router.callback_query(F.data == "sub:plans")
async def cb_show_plans(callback: CallbackQuery, session: AsyncSession) -> None:
    """Список тарифов."""
    sub_service = SubscriptionService(session)
    plans = await sub_service.get_plans()

    if not plans:
        await callback.message.edit_text(
            "📋 Тарифы пока не настроены.",
            reply_markup=subscription_kb(),
        )
        await callback.answer()
        return

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "  💎 <b>ТАРИФНЫЕ ПЛАНЫ</b>",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
    ]
    for plan in plans:
        emoji = {"free": "🆓", "premium": "💎", "vip": "👑"}.get(plan.slug, "📦")
        lines.append(
            f"{emoji} <b>{plan.name}</b>\n"
            f"    💰 ${plan.price_usd} / {plan.duration_days} дней\n"
            f"    📊 {plan.daily_request_limit} запросов/день\n"
            f"    🔍 OSINT: {'✅' if plan.osint_enabled else '❌'}\n"
        )

    lines.append("Выберите тариф:")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=plan_selection_kb(plans),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_plan(callback: CallbackQuery, session: AsyncSession) -> None:
    """Оформление заявки на подписку (ручная монетизация, без автоплатежей)."""
    plan_slug = callback.data.split(":", 1)[1]
    sub_service = SubscriptionService(session)
    plan = await sub_service.get_plan_by_slug(plan_slug)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    # Free не требует заявки — это тариф по умолчанию.
    if plan.slug == "free" or float(plan.price_usd) == 0:
        await callback.answer(
            "🆓 Free доступен сразу — заявка не нужна.", show_alert=True
        )
        return

    emoji = {"free": "🆓", "premium": "💎", "vip": "👑"}.get(plan.slug, "📦")
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {emoji} <b>{plan.name.upper()}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"┣ 💰 <b>Цена:</b> ${plan.price_usd}"
    )
    if plan.price_stars > 0:
        text += f" / {plan.price_stars} ⭐"
    text += (
        f"\n┣ 📅 <b>Период:</b> {plan.duration_days} дней\n"
        f"┣ 📊 <b>Лимит:</b> {plan.daily_request_limit} запросов/день\n"
        f"┗ 🔍 <b>OSINT:</b> {'✅ Доступен' if plan.osint_enabled else '❌'}\n\n"
        "💬 Оплата проходит вручную через владельца.\n"
        "Оставь заявку — мы свяжемся и активируем подписку."
    )

    await callback.message.edit_text(
        text,
        reply_markup=request_subscription_kb(plan_slug),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "sub:cancel")
async def cb_cancel_subscription(
    callback: CallbackQuery, session: AsyncSession, db_user: User,
) -> None:
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  ⚠️ <b>ОТМЕНА ПОДПИСКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
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
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "  ✅ <b>ПОДПИСКА ОТМЕНЕНА</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
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
        "━━━━━━━━━━━━━━━━━━━━━━",
        "  📜 <b>ИСТОРИЯ ПЛАТЕЖЕЙ</b>",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    if not payments:
        lines.append("Платежей пока нет.")
    else:
        for p in payments:
            dt = p.created_at.strftime("%d.%m.%Y %H:%M")
            icons = {"completed": "✅", "pending": "⏳", "failed": "❌", "refunded": "↩️"}
            icon = icons.get(p.status.value, "❓")
            lines.append(f"  {dt} — {icon} ${p.amount} ({p.payment_method})")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=subscription_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "sub:back")
async def cb_sub_back(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  💎 <b>ПОДПИСКА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите действие:",
        reply_markup=subscription_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("❌ Отменено.", reply_markup=nav_home_kb())
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
