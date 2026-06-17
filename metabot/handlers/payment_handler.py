"""
Vexis — Ручная монетизация.

Автоплатежи отключены намеренно: единственный владелец (Owner) принимает
решение по каждой заявке вручную. Поток:
  buy:{slug}  →  req:create:{slug}  →  уведомление Owner с кнопками
  req:approve:{id} / req:reject:{id}  (только Owner)  →  идемпотентная активация.

Хэндлеры pre_checkout / successful_payment оставлены как защитные заглушки:
если платёж как-то прилетит — подписка НЕ выдаётся автоматически.
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
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.keyboards import (
    subscription_kb,
    nav_home_kb,
    owner_request_decision_kb,
)
from metabot.models.user import User
from metabot.services.security_service import SecurityAuditService
from metabot.services.subscription_service import (
    SubscriptionService,
    DuplicateRequestError,
)
from metabot.configs import get_settings

logger = logging.getLogger(__name__)

router = Router(name="payments")


def _plan_emoji(slug: str) -> str:
    return {"free": "🆓", "premium": "💎", "vip": "👑"}.get(slug, "📦")


def _user_label(user: User) -> str:
    name = " ".join(filter(None, [user.first_name, user.last_name])) or "—"
    handle = f"@{user.username}" if user.username else "—"
    return f"{name} ({handle}, ID <code>{user.telegram_id}</code>)"


# ═══════════════════════════════════════════════════════════
#  ЗАЯВКА НА ПОДПИСКУ (пользователь)
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("req:create:"))
async def cb_create_request(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, db_user: User,
) -> None:
    """Создать заявку на подписку и уведомить Owner."""
    plan_slug = callback.data.split(":", 2)[2]
    settings = get_settings()
    sub_service = SubscriptionService(session)

    plan = await sub_service.get_plan_by_slug(plan_slug)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    if plan.slug == "free" or float(plan.price_usd) == 0:
        await callback.answer("🆓 Free доступен сразу — заявка не нужна.", show_alert=True)
        return

    try:
        req = await sub_service.create_request(db_user, plan)
    except DuplicateRequestError:
        await callback.answer(
            "У тебя уже есть активная заявка — дождись решения 🙏", show_alert=True
        )
        return

    # Подтверждение пользователю
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  ✅ <b>ЗАЯВКА ОТПРАВЛЕНА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{_plan_emoji(plan.slug)} <b>{plan.name}</b> — ${plan.price_usd}\n\n"
        "Владелец рассмотрит её вручную и активирует подписку после оплаты.\n"
        f"По вопросам оплаты: @{settings.support_contact}",
        reply_markup=nav_home_kb(),
        parse_mode="HTML",
    )
    await callback.answer("Заявка отправлена ✅")

    # Уведомление Owner с кнопками решения
    if settings.owner_id:
        try:
            await bot.send_message(
                chat_id=settings.owner_id,
                text=(
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "  🔔 <b>НОВАЯ ЗАЯВКА НА ПОДПИСКУ</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"┣ 🧾 <b>Заявка:</b> #{req.id}\n"
                    f"┣ 👤 <b>Пользователь:</b> {_user_label(db_user)}\n"
                    f"┣ {_plan_emoji(plan.slug)} <b>Тариф:</b> {plan.name}\n"
                    f"┣ 💰 <b>Цена:</b> ${plan.price_usd}"
                    + (f" / {plan.price_stars} ⭐" if plan.price_stars else "")
                    + f"\n┗ 📅 <b>Период:</b> {plan.duration_days} дней"
                ),
                reply_markup=owner_request_decision_kb(req.id),
                parse_mode="HTML",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to notify owner about request %d: %s", req.id, e)
    else:
        logger.warning("OWNER_ID not configured — request %d created without notify", req.id)


# ═══════════════════════════════════════════════════════════
#  РЕШЕНИЕ ПО ЗАЯВКЕ (только Owner)
# ═══════════════════════════════════════════════════════════

async def _guard_owner(callback: CallbackQuery) -> bool:
    settings = get_settings()
    if not settings.owner_id or callback.from_user.id != settings.owner_id:
        await callback.answer("⛔ Только владелец может принимать решения.", show_alert=True)
        return False
    return True


@router.callback_query(F.data.startswith("req:approve:"))
async def cb_approve_request(
    callback: CallbackQuery, bot: Bot, session: AsyncSession,
) -> None:
    if not await _guard_owner(callback):
        return
    settings = get_settings()
    try:
        request_id = int(callback.data.split(":", 2)[2])
    except ValueError:
        await callback.answer("Некорректная заявка", show_alert=True)
        return

    sub_service = SubscriptionService(session)
    outcome, req = await sub_service.approve_request(request_id, settings.owner_id)

    if outcome == "not_found":
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if outcome == "already":
        await callback.answer("Уже одобрена ранее ✅", show_alert=True)
        return
    if outcome == "not_pending":
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    # outcome == "approved"
    plan = req.plan
    await SecurityAuditService(session).record(
        action="subscription_approved",
        actor_tg_id=settings.owner_id,
        actor_role="owner",
        target_user_id=req.user_id,
        target_type="subscription_request",
        target_id=str(req.id),
        after={"plan": plan.slug, "subscription_id": req.subscription_id},
        note=f"plan={plan.slug}",
    )
    await callback.message.edit_text(
        f"✅ <b>Заявка #{req.id} одобрена</b>\n"
        f"Подписка <b>{plan.name}</b> активирована.",
        parse_mode="HTML",
    )
    await callback.answer("Одобрено ✅")

    # Уведомить пользователя
    user = await session.get(User, req.user_id)
    if user:
        try:
            await bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "  🎉 <b>ПОДПИСКА АКТИВИРОВАНА!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"{_plan_emoji(plan.slug)} <b>{plan.name}</b>\n"
                    f"📊 {plan.daily_request_limit} запросов/день\n\n"
                    "Приятного использования Vexis 🚀"
                ),
                reply_markup=subscription_kb(has_active=True),
                parse_mode="HTML",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to notify user %d about approval: %s", user.telegram_id, e)


@router.callback_query(F.data.startswith("req:reject:"))
async def cb_reject_request(
    callback: CallbackQuery, bot: Bot, session: AsyncSession,
) -> None:
    if not await _guard_owner(callback):
        return
    settings = get_settings()
    try:
        request_id = int(callback.data.split(":", 2)[2])
    except ValueError:
        await callback.answer("Некорректная заявка", show_alert=True)
        return

    sub_service = SubscriptionService(session)
    outcome, req = await sub_service.reject_request(request_id, settings.owner_id)

    if outcome == "not_found":
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if outcome == "already":
        await callback.answer("Уже отклонена ранее", show_alert=True)
        return
    if outcome == "not_pending":
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    # outcome == "rejected"
    await SecurityAuditService(session).record(
        action="subscription_rejected",
        actor_tg_id=settings.owner_id,
        actor_role="owner",
        target_user_id=req.user_id,
        target_type="subscription_request",
        target_id=str(req.id),
        note="rejected by owner",
    )
    await callback.message.edit_text(
        f"❌ <b>Заявка #{req.id} отклонена.</b>",
        parse_mode="HTML",
    )
    await callback.answer("Отклонено")

    user = await session.get(User, req.user_id)
    if user:
        try:
            await bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    "❌ <b>Заявка на подписку отклонена.</b>\n\n"
                    f"По вопросам обращайся: @{settings.support_contact}"
                ),
                reply_markup=nav_home_kb(),
                parse_mode="HTML",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to notify user %d about rejection: %s", user.telegram_id, e)


# ═══════════════════════════════════════════════════════════
#  ЗАЩИТНЫЕ ЗАГЛУШКИ: автоплатежи отключены
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("pay:"))
async def cb_payment_disabled(callback: CallbackQuery) -> None:
    """Автоматическая оплата отключена — направляем на ручную заявку."""
    settings = get_settings()
    await callback.answer(
        f"Оплата проходит вручную. Оставь заявку или напиши @{settings.support_contact}.",
        show_alert=True,
    )


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    """Автоплатежи отключены — отклоняем pre-checkout."""
    settings = get_settings()
    await pre_checkout_query.answer(
        ok=False,
        error_message=(
            f"Оплата проходит вручную через @{settings.support_contact}. "
            "Оставьте заявку в боте."
        ),
    )


@router.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def successful_payment(message: Message, db_user: User) -> None:
    """Защита: если платёж как-то прошёл — НЕ выдаём подписку автоматически."""
    settings = get_settings()
    payment = message.successful_payment
    charge_id = (
        getattr(payment, "provider_payment_charge_id", None)
        or getattr(payment, "telegram_payment_charge_id", None)
    )
    logger.warning(
        "Unexpected successful_payment (auto-grant disabled): user=%d charge=%s",
        db_user.telegram_id, charge_id,
    )
    await message.answer(
        "⚠️ Платёж получен, но активация подписок проходит вручную.\n"
        f"Напиши владельцу с этим ID платежа: <code>{charge_id}</code>\n"
        f"Контакт: @{settings.support_contact}",
        parse_mode="HTML",
        reply_markup=nav_home_kb(),
    )
