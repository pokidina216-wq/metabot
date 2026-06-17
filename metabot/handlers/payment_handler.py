"""
Vexis — Подписки и промокоды.

Поток покупки подписки:
  1. Пользователь видит тарифы с ценами
  2. Нажимает «Написать для покупки» → получает контакт Owner в ТГ
  3. Нажимает «Ввести промокод» → вводит код → подписка активируется

Автоплатежи ОТКЛЮЧЕНЫ. Owner выдаёт подписки вручную через админ-панель
или создаёт промокоды.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
    PreCheckoutQuery,
    ContentType,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.keyboards import nav_home_kb, subscription_kb
from metabot.models.user import User
from metabot.services.subscription_service import SubscriptionService
from metabot.configs import get_settings
from metabot.utils.validators import sanitize

logger = logging.getLogger(__name__)

router = Router(name="payments")


class PromoStates(StatesGroup):
    waiting_code = State()


# ═══════════════════════════════════════════════════════════
#  ТАРИФЫ — показать цены
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "sub:plans")
async def cb_show_plans(callback: CallbackQuery, **data) -> None:
    """Показать тарифы с новыми ценами."""
    settings = get_settings()
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  💎 <b>ТАРИФНЫЕ ПЛАНЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆓 <b>Free</b>\n"
        "   ├ 5 запросов/день\n"
        "   ├ Базовые инструменты\n"
        "   └ Бесплатно навсегда\n\n"
        "💎 <b>Premium</b> — <code>$6</code> / 30 дней\n"
        "   ├ 50 запросов/день\n"
        "   ├ Все OSINT-инструменты\n"
        "   ├ Анализ метаданных без лимитов\n"
        "   └ Приоритетная поддержка\n\n"
        "👑 <b>VIP</b> — <code>$15</code> / 90 дней\n"
        "   ├ 200 запросов/день\n"
        "   ├ Все инструменты + API\n"
        "   ├ Ранний доступ к новым функциям\n"
        "   └ Персональная поддержка\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 Для покупки напишите: @{settings.support_contact}"
    )
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(
        text=f"💬 Написать @{settings.support_contact}",
        url=f"https://t.me/{settings.support_contact}",
    )
    b.button(text="🎟 Ввести промокод", callback_data="promo:enter")
    b.button(text="← Назад", callback_data="nav:home")
    b.adjust(1)

    await callback.message.edit_text(
        text, reply_markup=b.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_redirect(callback: CallbackQuery, **data) -> None:
    """Пользователь нажал на план — направляем к Owner."""
    settings = get_settings()
    slug = callback.data.split(":", 1)[1]
    plan_names = {"premium": "Premium", "vip": "VIP"}
    plan_name = plan_names.get(slug, slug)

    await callback.answer(
        f"Для покупки {plan_name} напишите @{settings.support_contact}",
        show_alert=True,
    )


# ═══════════════════════════════════════════════════════════
#  ПРОМОКОД — ввод и активация
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "promo:enter")
async def cb_promo_enter(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать ввод промокода."""
    await state.set_state(PromoStates.waiting_code)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="promo:cancel")
    b.adjust(1)

    await callback.message.edit_text(
        "🎟 <b>Введите промокод</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Напишите промокод в чат:\n\n"
        "<i>Пример: <code>VEXIS2026</code></i>",
        reply_markup=b.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "promo:cancel")
async def cb_promo_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отменить ввод промокода."""
    await state.clear()
    await callback.message.edit_text(
        "❌ Ввод промокода отменён.",
        reply_markup=nav_home_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(PromoStates.waiting_code)
async def process_promo_code(
    message: Message, state: FSMContext, session: AsyncSession, db_user: User, **data,
) -> None:
    """Обработка введённого промокода."""
    await state.clear()
    code = (message.text or "").strip()

    if not code or len(code) > 32:
        await message.answer(
            "⚠️ Некорректный промокод.",
            reply_markup=nav_home_kb(),
        )
        return

    sub_service = SubscriptionService(session)
    outcome, sub = await sub_service.activate_promo(db_user, code)

    if outcome == "not_found":
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        b = InlineKeyboardBuilder()
        b.button(text="🔄 Попробовать другой", callback_data="promo:enter")
        b.button(text="🏠 На главную", callback_data="nav:home")
        b.adjust(1)
        await message.answer(
            "❌ <b>Промокод не найден</b>\n\n"
            "Код недействителен, истёк или уже использован.",
            reply_markup=b.as_markup(),
            parse_mode="HTML",
        )
        return

    # outcome == "activated"
    plan = sub.plan
    days = (sub.expires_at - sub.starts_at).days
    emoji = {"premium": "💎", "vip": "👑"}.get(plan.slug, "📦")

    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🎉 <b>ПРОМОКОД АКТИВИРОВАН!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{emoji} <b>Тариф:</b> {sanitize(plan.name, 32)}\n"
        f"📅 <b>Период:</b> {days} дней\n"
        f"📊 <b>Лимит:</b> {plan.daily_request_limit} запросов/день\n\n"
        "Приятного использования Vexis! 🚀",
        reply_markup=nav_home_kb(),
        parse_mode="HTML",
    )

    # Уведомить Owner
    settings = get_settings()
    if settings.owner_id:
        try:
            bot: Bot = message.bot
            safe_name = sanitize(
                (message.from_user.first_name or "") + " " + (message.from_user.last_name or ""),
                64,
            ).strip() or "—"
            handle = f"@{message.from_user.username}" if message.from_user.username else "—"
            await bot.send_message(
                chat_id=settings.owner_id,
                text=(
                    "🎟 <b>Промокод использован</b>\n\n"
                    f"👤 {safe_name} ({handle})\n"
                    f"🆔 <code>{message.from_user.id}</code>\n"
                    f"🎟 Код: <code>{sanitize(code, 32)}</code>\n"
                    f"{emoji} Тариф: {plan.name} / {days} дней"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Failed to notify owner about promo usage: %s", e)


# ═══════════════════════════════════════════════════════════
#  ЗАЩИТНЫЕ ЗАГЛУШКИ: автоплатежи отключены
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("pay:"))
async def cb_payment_disabled(callback: CallbackQuery) -> None:
    """Автоматическая оплата отключена."""
    settings = get_settings()
    await callback.answer(
        f"Оплата проходит вручную. Напишите @{settings.support_contact}.",
        show_alert=True,
    )


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    """Автоплатежи отключены — отклоняем."""
    settings = get_settings()
    await pre_checkout_query.answer(
        ok=False,
        error_message=f"Оплата вручную через @{settings.support_contact}.",
    )


@router.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment(message: Message, db_user: User) -> None:
    """Защита: если платёж прошёл — НЕ выдаём автоматически."""
    settings = get_settings()
    payment = message.successful_payment
    charge_id = (
        getattr(payment, "provider_payment_charge_id", None)
        or getattr(payment, "telegram_payment_charge_id", None)
    )
    logger.warning(
        "Unexpected payment (auto-grant disabled): user=%d charge=%s",
        db_user.telegram_id, charge_id,
    )
    await message.answer(
        "⚠️ Платёж получен, но подписка активируется вручную.\n"
        f"Напишите владельцу: @{settings.support_contact}\n"
        f"ID платежа: <code>{charge_id}</code>",
        parse_mode="HTML",
        reply_markup=nav_home_kb(),
    )
