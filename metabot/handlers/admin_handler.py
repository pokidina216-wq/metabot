"""
Админ-панель — полное управление ботом для Owner.

Изменения v2:
- Доступ через RBAC (has_permission), не через is_admin().
- Dashboard с метриками по умолчанию.
- Очередь ожидающих заявок (pending requests) со счётчиком.
- Разбан через меню.
- Пагинация пользователей.
- Broadcast с подтверждением (confirmation token), FloodWait, задержкой.
- Полный аудит с actor_user_id (из db_user).
"""
from __future__ import annotations

import asyncio
import json
import logging

from aiogram import Bot, Router, F
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.configs import get_settings
from metabot.models.user import User, UserRole
from metabot.repositories.user_repo import UserRepository
from metabot.repositories.osint_repo import OsintSourceRepository
from metabot.services.analytics_service import AnalyticsService
from metabot.services.subscription_service import SubscriptionService
from metabot.services.security_service import SecurityAuditService, OwnerProtectionError
from metabot.security.roles import Permission, has_permission
from metabot.security.confirm import issue_token, consume_token
from metabot.utils.validators import sanitize

logger = logging.getLogger(__name__)

router = Router(name="admin")

# Кол-во пользователей на страницу
USERS_PER_PAGE = 15
# Задержка между сообщениями в рассылке (Telegram лимит ~30/сек)
BROADCAST_DELAY = 0.05  # 50мс = ~20/сек (с запасом)


# ── Хелперы ────────────────────────────────────────────────

def _check(data: dict, permission: Permission) -> bool:
    """Проверить право через данные из RBACMiddleware."""
    role = data.get("effective_role", UserRole.USER)
    tg_id = getattr(getattr(data.get("event_context"), "from_user", None), "id", None)
    owner_id = get_settings().owner_id
    return has_permission(role, permission, telegram_id=tg_id, owner_id=owner_id)


def _can_admin(data: dict) -> bool:
    """Быстрая проверка: есть ли VIEW_USERS (минимум для входа в админку)."""
    role = data.get("effective_role", UserRole.USER)
    if role == UserRole.OWNER:
        return True
    return has_permission(role, Permission.VIEW_USERS)


def _actor_id(data: dict) -> int | None:
    """Получить db user id из middleware-данных."""
    db_user = data.get("db_user")
    return db_user.id if db_user else None


def _actor_tg_id(data: dict) -> int | None:
    db_user = data.get("db_user")
    return db_user.telegram_id if db_user else None


class AdminStates(StatesGroup):
    search_query = State()
    broadcast_text = State()
    broadcast_confirm = State()
    ban_user_id = State()
    ban_reason = State()
    unban_user_id = State()
    grant_sub_user_id = State()
    grant_sub_plan = State()
    add_source_name = State()
    add_source_slug = State()
    add_source_type = State()
    add_source_category = State()
    add_source_config = State()


# ═══════════════════════════════════════════════════════════
#  ADMIN MENU (с inline-клавиатурой)
# ═══════════════════════════════════════════════════════════

def _admin_dashboard_kb():
    """Клавиатура админ-меню с бейджем ожидающих заявок."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    buttons = [
        ("📊 Аналитика",            "admin:analytics"),
        ("📬 Заявки",               "admin:pending"),
        ("👥 Пользователи",         "admin:users:1"),
        ("🔍 Поиск",                "admin:search"),
        ("💎 Выдать подписку",       "admin:subscriptions"),
        ("🚫 Бан",                  "admin:ban"),
        ("✅ Разбан",               "admin:unban"),
        ("📢 Рассылка",            "admin:broadcast"),
        ("🔧 Источники OSINT",     "admin:osint_sources"),
        ("⚙️ Тарифы",              "admin:plans"),
        ("📋 Логи",                 "admin:logs"),
    ]
    for text, cb in buttons:
        b.button(text=text, callback_data=cb)
    b.button(text="🏠 На главную", callback_data="nav:home")
    b.adjust(2, 2, 2, 2, 2, 1, 1)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  ВХОД В АДМИН-ПАНЕЛЬ → Dashboard
# ═══════════════════════════════════════════════════════════

@router.message(Command("admin"))
async def cmd_admin(message: Message, session: AsyncSession, **data) -> None:
    if not _can_admin(data):
        await message.answer("🚫 Доступ запрещён.")
        return
    await _show_dashboard(message, session, edit=False)


@router.callback_query(F.data == "admin:menu")
async def cb_admin_menu(callback: CallbackQuery, session: AsyncSession, **data) -> None:
    if not _can_admin(data):
        await callback.answer("🚫", show_alert=True)
        return
    await _show_dashboard(callback.message, session, edit=True)
    await callback.answer()


async def _show_dashboard(
    target: Message, session: AsyncSession, *, edit: bool = False
) -> None:
    """Показать dashboard с ключевыми метриками."""
    analytics = AnalyticsService(session)
    stats = await analytics.get_dashboard()

    # Считаем ожидающие заявки
    from metabot.repositories.subscription_repo import SubscriptionRequestRepository
    req_repo = SubscriptionRequestRepository(session)
    pending = await req_repo.list_pending(limit=100)
    pending_count = len(pending)
    pending_badge = f"  🔴 {pending_count}" if pending_count else ""

    text = (
        "🛠 <b>Панель управления Vexis</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Пользователи: <b>{stats.total_users:,}</b>  "
        f"(+{stats.new_users_today} сегодня)\n"
        f"📈 Запросов сегодня: <b>{stats.requests_today:,}</b>\n"
        f"💰 Доход за неделю: <b>${stats.revenue_week:.2f}</b>\n"
        f"📬 Ожидающие заявки: <b>{pending_count}</b>{pending_badge}\n"
    )

    if stats.popular_tools:
        text += "\n🔧 Топ инструменты: "
        text += ", ".join(f"{a}({c})" for a, c in stats.popular_tools[:3])
        text += "\n"

    if edit:
        await target.edit_text(text, reply_markup=_admin_dashboard_kb(), parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=_admin_dashboard_kb(), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════
#  АНАЛИТИКА (расширенная)
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:analytics")
async def cb_analytics(callback: CallbackQuery, session: AsyncSession, **data) -> None:
    if not _can_admin(data):
        await callback.answer("🚫", show_alert=True)
        return

    analytics = AnalyticsService(session)
    stats = await analytics.get_dashboard()

    text = (
        "📊 <b>Аналитика</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Пользователи</b>\n"
        f"  Всего: {stats.total_users:,}\n"
        f"  Новых сегодня: +{stats.new_users_today}\n"
        f"  Новых за неделю: +{stats.new_users_week}\n"
        f"  Активных сегодня: {stats.active_today}\n"
        f"  Активных за неделю: {stats.active_week}\n\n"
        f"📈 <b>Запросы</b>\n"
        f"  Сегодня: {stats.requests_today:,}\n"
        f"  За неделю: {stats.requests_week:,}\n\n"
        f"💰 <b>Доход</b>\n"
        f"  Сегодня: ${stats.revenue_today:.2f}\n"
        f"  За неделю: ${stats.revenue_week:.2f}\n"
    )

    if stats.popular_tools:
        text += "\n🔧 <b>Популярные инструменты (неделя)</b>\n"
        for action, cnt in stats.popular_tools:
            text += f"  • {action}: {cnt}\n"

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(text="← Назад", callback_data="admin:menu")
    await callback.message.edit_text(text, reply_markup=b.as_markup(), parse_mode="HTML")
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  ОЖИДАЮЩИЕ ЗАЯВКИ (pending requests)
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:pending")
async def cb_pending_requests(callback: CallbackQuery, session: AsyncSession, **data) -> None:
    if not _can_admin(data):
        await callback.answer("🚫", show_alert=True)
        return

    from metabot.repositories.subscription_repo import SubscriptionRequestRepository
    req_repo = SubscriptionRequestRepository(session)
    pending = await req_repo.list_pending(limit=50)

    if not pending:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        b = InlineKeyboardBuilder()
        b.button(text="← Назад", callback_data="admin:menu")
        await callback.message.edit_text(
            "📬 <b>Ожидающие заявки</b>\n\nНет ожидающих заявок. ✅",
            reply_markup=b.as_markup(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    lines = [f"📬 <b>Ожидающие заявки</b> ({len(pending)})\n"]

    b = InlineKeyboardBuilder()
    for req in pending[:10]:  # Показываем первые 10
        user = req.user
        plan = req.plan
        username = f"@{user.username}" if user and user.username else f"ID:{req.user_id}"
        plan_name = plan.name if plan else "?"
        dt = req.created_at.strftime("%d.%m %H:%M") if req.created_at else "?"
        lines.append(f"  #{req.id} — {username} → <b>{plan_name}</b> ({dt})")

        b.button(text=f"✅ #{req.id}", callback_data=f"req:approve:{req.id}")
        b.button(text=f"❌ #{req.id}", callback_data=f"req:reject:{req.id}")

    b.adjust(2)
    b.row()
    b.button(text="← Назад", callback_data="admin:menu")

    if len(pending) > 10:
        lines.append(f"\n... и ещё {len(pending) - 10} заявок")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=b.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


# ── Одобрение / Отклонение заявок ──────────────────────────

@router.callback_query(F.data.startswith("req:approve:"))
async def cb_approve_request(callback: CallbackQuery, session: AsyncSession, **data) -> None:
    if not has_permission(data.get("effective_role", UserRole.USER), Permission.APPROVE_SUBSCRIPTION):
        await callback.answer("🚫 Нет прав", show_alert=True)
        return

    req_id = int(callback.data.split(":")[2])
    sub_service = SubscriptionService(session)
    actor_tg = _actor_tg_id(data) or 0
    try:
        outcome, req = await sub_service.approve_request(req_id, owner_id=actor_tg)
        if outcome != "approved":
            await callback.answer(f"ℹ️ Статус: {outcome}", show_alert=True)
            return
        audit = SecurityAuditService(session)
        await audit.record(
            action="approve_subscription_request",
            actor_tg_id=_actor_tg_id(data),
            actor_user_id=_actor_id(data),
            after={"request_id": req_id},
        )
        await callback.answer(f"✅ Заявка #{req_id} одобрена!", show_alert=True)
    except Exception as e:
        await callback.answer(f"⚠️ Ошибка: {str(e)[:100]}", show_alert=True)
        return

    # Обновляем список pending
    await cb_pending_requests(callback, session, **data)


@router.callback_query(F.data.startswith("req:reject:"))
async def cb_reject_request(callback: CallbackQuery, session: AsyncSession, **data) -> None:
    if not has_permission(data.get("effective_role", UserRole.USER), Permission.APPROVE_SUBSCRIPTION):
        await callback.answer("🚫 Нет прав", show_alert=True)
        return

    req_id = int(callback.data.split(":")[2])
    sub_service = SubscriptionService(session)
    actor_tg = _actor_tg_id(data) or 0
    try:
        outcome, req = await sub_service.reject_request(req_id, owner_id=actor_tg)
        if outcome != "rejected":
            await callback.answer(f"ℹ️ Статус: {outcome}", show_alert=True)
            return
        audit = SecurityAuditService(session)
        await audit.record(
            action="reject_subscription_request",
            actor_tg_id=_actor_tg_id(data),
            actor_user_id=_actor_id(data),
            after={"request_id": req_id},
        )
        await callback.answer(f"❌ Заявка #{req_id} отклонена.", show_alert=True)
    except Exception as e:
        await callback.answer(f"⚠️ Ошибка: {str(e)[:100]}", show_alert=True)
        return

    await cb_pending_requests(callback, session, **data)


# ═══════════════════════════════════════════════════════════
#  ПОЛЬЗОВАТЕЛИ с пагинацией
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("admin:users:"))
async def cb_users_page(callback: CallbackQuery, session: AsyncSession, **data) -> None:
    if not _can_admin(data):
        await callback.answer("🚫", show_alert=True)
        return

    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 1

    repo = UserRepository(session)
    total = await repo.count()
    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * USERS_PER_PAGE

    users = await repo.get_many(offset=offset, limit=USERS_PER_PAGE, order_by=User.created_at.desc())

    lines = [f"👥 <b>Пользователи</b> ({total:,}) — стр. {page}/{total_pages}\n"]
    for u in users:
        ban_icon = "🚫" if u.is_banned else ""
        role_icon = {
            "owner": "👑", "admin": "🔑", "moderator": "🛡", "premium": "💎"
        }.get(u.role.value, "")
        name = f"@{u.username}" if u.username else (u.first_name or "N/A")
        lines.append(
            f"  {role_icon}{ban_icon} <code>{u.telegram_id}</code> — "
            f"{name} ({u.total_requests} запр.)"
        )

    # Пагинация
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    if page > 1:
        b.button(text="◀️", callback_data=f"admin:users:{page - 1}")
    b.button(text=f"{page}/{total_pages}", callback_data="noop")
    if page < total_pages:
        b.button(text="▶️", callback_data=f"admin:users:{page + 1}")
    b.adjust(3)
    b.row()
    b.button(text="← Назад", callback_data="admin:menu")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=b.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  ПОИСК
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:search")
async def cb_admin_search(callback: CallbackQuery, state: FSMContext, **data) -> None:
    if not _can_admin(data):
        await callback.answer("🚫", show_alert=True)
        return
    await callback.message.edit_text(
        "🔍 Введите username, имя или Telegram ID для поиска:"
    )
    await state.set_state(AdminStates.search_query)
    await callback.answer()


@router.message(AdminStates.search_query)
async def process_admin_search(message: Message, state: FSMContext, session: AsyncSession, **data) -> None:
    if not _can_admin(data):
        return
    await state.clear()

    query = sanitize(message.text, max_len=64)
    if not query:
        await message.answer("⚠️ Пустой запрос.", reply_markup=_admin_dashboard_kb())
        return

    repo = UserRepository(session)
    users = await repo.search(query)

    if not users:
        await message.answer(
            f"🔍 По запросу «{query}» ничего не найдено.",
            reply_markup=_admin_dashboard_kb(),
        )
        return

    lines = [f"🔍 <b>Результаты:</b> «{query}» ({len(users)})\n"]
    for u in users[:20]:
        ban = "🚫" if u.is_banned else ""
        role = {"owner": "👑", "admin": "🔑", "moderator": "🛡"}.get(u.role.value, "")
        lines.append(
            f"  {role}{ban} <code>{u.telegram_id}</code> — "
            f"@{u.username or 'N/A'} | {u.first_name or ''}\n"
            f"      Запросов: {u.total_requests} | Роль: {u.role.value}"
        )

    await message.answer("\n".join(lines), reply_markup=_admin_dashboard_kb(), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════
#  ВЫДАЧА ПОДПИСКИ
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:subscriptions")
async def cb_admin_subs(callback: CallbackQuery, state: FSMContext, **data) -> None:
    if not has_permission(
        data.get("effective_role", UserRole.USER),
        Permission.APPROVE_SUBSCRIPTION
    ):
        await callback.answer("🚫 Нет прав", show_alert=True)
        return

    await callback.message.edit_text(
        "💎 <b>Выдача подписки</b>\n\nВведите Telegram ID пользователя:",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.grant_sub_user_id)
    await callback.answer()


@router.message(AdminStates.grant_sub_user_id)
async def process_grant_user_id(message: Message, state: FSMContext, **data) -> None:
    if not _can_admin(data):
        return
    try:
        tg_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введите числовой Telegram ID:")
        return
    await state.update_data(grant_tg_id=tg_id)
    await message.answer("Введите slug тарифа (premium / vip):")
    await state.set_state(AdminStates.grant_sub_plan)


@router.message(AdminStates.grant_sub_plan)
async def process_grant_plan(
    message: Message, state: FSMContext, session: AsyncSession, **data
) -> None:
    if not _can_admin(data):
        return

    plan_slug = message.text.strip().lower()
    fsm_data = await state.get_data()
    tg_id = fsm_data["grant_tg_id"]
    await state.clear()

    repo = UserRepository(session)
    user = await repo.get_by_telegram_id(tg_id)
    if not user:
        await message.answer(f"❌ Пользователь {tg_id} не найден.", reply_markup=_admin_dashboard_kb())
        return

    sub_service = SubscriptionService(session)
    plan = await sub_service.get_plan_by_slug(plan_slug)
    if not plan:
        await message.answer(f"❌ Тариф «{plan_slug}» не найден.", reply_markup=_admin_dashboard_kb())
        return

    sub = await sub_service.activate_subscription(
        user=user, plan=plan, payment_method="admin_grant"
    )

    # Аудит
    audit = SecurityAuditService(session)
    await audit.record(
        action="grant_subscription",
        actor_tg_id=_actor_tg_id(data),
        actor_user_id=_actor_id(data),
        actor_role=str(data.get("effective_role", "?")),
        target_user_id=user.id,
        target_tg_id=user.telegram_id,
        after={"plan": plan_slug, "expires_at": sub.expires_at.isoformat()},
    )

    await message.answer(
        f"✅ Подписка <b>{plan.name}</b> выдана пользователю <code>{tg_id}</code>\n"
        f"Активна до: {sub.expires_at.strftime('%d.%m.%Y %H:%M')}",
        reply_markup=_admin_dashboard_kb(),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════
#  БАН
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:ban")
async def cb_admin_ban(callback: CallbackQuery, state: FSMContext, **data) -> None:
    if not has_permission(data.get("effective_role", UserRole.USER), Permission.BAN_USER):
        await callback.answer("🚫 Нет прав", show_alert=True)
        return

    await callback.message.edit_text("🚫 Введите Telegram ID для бана:")
    await state.set_state(AdminStates.ban_user_id)
    await callback.answer()


@router.message(AdminStates.ban_user_id)
async def process_ban_id(message: Message, state: FSMContext, **data) -> None:
    if not _can_admin(data):
        return
    try:
        tg_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введите числовой ID:")
        return
    await state.update_data(ban_tg_id=tg_id)
    await message.answer("Введите причину бана (или «-» без причины):")
    await state.set_state(AdminStates.ban_reason)


@router.message(AdminStates.ban_reason)
async def process_ban_reason(
    message: Message, state: FSMContext, session: AsyncSession, **data
) -> None:
    if not _can_admin(data):
        return

    reason = message.text.strip()
    if reason == "-":
        reason = None

    fsm_data = await state.get_data()
    tg_id = fsm_data["ban_tg_id"]
    await state.clear()

    repo = UserRepository(session)
    user = await repo.get_by_telegram_id(tg_id)
    if not user:
        await message.answer(f"❌ Пользователь {tg_id} не найден.", reply_markup=_admin_dashboard_kb())
        return

    # Защита Owner
    settings = get_settings()
    audit = SecurityAuditService(session)
    try:
        await audit.assert_not_owner_target(
            user, settings.owner_id, op="ban", actor_tg_id=message.from_user.id
        )
    except OwnerProtectionError:
        await session.commit()
        await message.answer(
            "🛡 Этого пользователя нельзя забанить — это владелец.",
            reply_markup=_admin_dashboard_kb(),
        )
        return

    before = {"is_banned": user.is_banned, "ban_reason": user.ban_reason}
    await repo.ban_user(user.id, reason)

    await audit.record(
        action="ban_user",
        actor_tg_id=_actor_tg_id(data),
        actor_user_id=_actor_id(data),
        actor_role=str(data.get("effective_role", "?")),
        target_user_id=user.id,
        target_tg_id=user.telegram_id,
        target_type="user",
        before=before,
        after={"is_banned": True, "ban_reason": reason},
    )

    await message.answer(
        f"🚫 Пользователь <code>{tg_id}</code> забанен.\nПричина: {reason or 'N/A'}",
        reply_markup=_admin_dashboard_kb(),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════
#  РАЗБАН (НОВЫЙ)
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:unban")
async def cb_admin_unban(callback: CallbackQuery, state: FSMContext, **data) -> None:
    if not has_permission(data.get("effective_role", UserRole.USER), Permission.UNBAN_USER):
        await callback.answer("🚫 Нет прав", show_alert=True)
        return

    await callback.message.edit_text("✅ Введите Telegram ID для разбана:")
    await state.set_state(AdminStates.unban_user_id)
    await callback.answer()


@router.message(AdminStates.unban_user_id)
async def process_unban(
    message: Message, state: FSMContext, session: AsyncSession, **data
) -> None:
    if not _can_admin(data):
        return
    await state.clear()

    try:
        tg_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введите числовой ID:", reply_markup=_admin_dashboard_kb())
        return

    repo = UserRepository(session)
    user = await repo.get_by_telegram_id(tg_id)
    if not user:
        await message.answer(f"❌ Пользователь {tg_id} не найден.", reply_markup=_admin_dashboard_kb())
        return

    if not user.is_banned:
        await message.answer(
            f"ℹ️ Пользователь <code>{tg_id}</code> не забанен.",
            reply_markup=_admin_dashboard_kb(),
            parse_mode="HTML",
        )
        return

    before = {"is_banned": user.is_banned, "ban_reason": user.ban_reason}
    await repo.unban_user(user.id)

    audit = SecurityAuditService(session)
    await audit.record(
        action="unban_user",
        actor_tg_id=_actor_tg_id(data),
        actor_user_id=_actor_id(data),
        actor_role=str(data.get("effective_role", "?")),
        target_user_id=user.id,
        target_tg_id=user.telegram_id,
        target_type="user",
        before=before,
        after={"is_banned": False, "ban_reason": None},
    )

    await message.answer(
        f"✅ Пользователь <code>{tg_id}</code> разбанен.",
        reply_markup=_admin_dashboard_kb(),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════
#  РАССЫЛКА (с подтверждением + FloodWait)
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast(callback: CallbackQuery, state: FSMContext, **data) -> None:
    if not has_permission(data.get("effective_role", UserRole.USER), Permission.BROADCAST):
        await callback.answer("🚫 Нет прав", show_alert=True)
        return

    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\n"
        "Введите текст сообщения (HTML).\n"
        "Отправьте «отмена» для отмены.",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.broadcast_text)
    await callback.answer()


@router.message(AdminStates.broadcast_text)
async def process_broadcast_text(
    message: Message, state: FSMContext, session: AsyncSession, **data
) -> None:
    if not _can_admin(data):
        return

    text = message.text
    if not text or text.strip().lower() == "отмена":
        await state.clear()
        await message.answer("❌ Рассылка отменена.", reply_markup=_admin_dashboard_kb())
        return

    # Сохраняем текст и выдаём токен подтверждения
    actor_tg = message.from_user.id
    token = await issue_token("broadcast", actor_tg, ttl=120)

    repo = UserRepository(session)
    total = await repo.count()

    await state.update_data(broadcast_text=text, broadcast_token=token)
    await state.set_state(AdminStates.broadcast_confirm)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить рассылку", callback_data=f"admin:broadcast_go:{token}")
    b.button(text="❌ Отмена", callback_data="admin:broadcast_cancel")
    b.adjust(1)

    await message.answer(
        f"📢 <b>Подтверждение рассылки</b>\n\n"
        f"Получателей: <b>{total:,}</b>\n"
        f"Текст:\n<blockquote>{sanitize(text[:500])}</blockquote>\n\n"
        f"⚠️ Токен истекает через 2 минуты.",
        reply_markup=b.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin:broadcast_go:"))
async def cb_broadcast_go(
    callback: CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession, **data
) -> None:
    if not _can_admin(data):
        await callback.answer("🚫", show_alert=True)
        return

    token = callback.data.split(":", 2)[2]
    actor_tg = callback.from_user.id

    # Проверяем одноразовый токен
    valid = await consume_token("broadcast", actor_tg, token)
    if not valid:
        await callback.answer("⏰ Токен истёк или уже использован.", show_alert=True)
        await state.clear()
        return

    fsm_data = await state.get_data()
    text = fsm_data.get("broadcast_text", "")
    await state.clear()

    if not text:
        await callback.answer("⚠️ Текст рассылки пуст.", show_alert=True)
        return

    await callback.answer("📢 Запускаю рассылку...")

    repo = UserRepository(session)
    total = await repo.count()

    status_msg = await callback.message.edit_text(
        f"📢 Рассылка... 0/{total}"
    )

    sent, failed, blocked = 0, 0, 0
    offset = 0
    batch_size = 100

    while True:
        users = await repo.get_many(offset=offset, limit=batch_size)
        if not users:
            break

        for user in users:
            try:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode="HTML",
                )
                sent += 1
            except TelegramRetryAfter as e:
                # FloodWait — ждём столько, сколько просит Telegram
                logger.warning("FloodWait: %d seconds", e.retry_after)
                await asyncio.sleep(e.retry_after)
                try:
                    await bot.send_message(
                        chat_id=user.telegram_id, text=text, parse_mode="HTML"
                    )
                    sent += 1
                except Exception:
                    failed += 1
            except TelegramForbiddenError:
                blocked += 1
            except Exception:
                failed += 1

            await asyncio.sleep(BROADCAST_DELAY)

        offset += batch_size

        # Обновляем статус каждые 500
        if offset % 500 == 0:
            try:
                await status_msg.edit_text(
                    f"📢 Рассылка... {sent + failed + blocked}/{total}\n"
                    f"✅ {sent}  ❌ {failed}  🚫 {blocked}"
                )
            except Exception:
                pass

    # Аудит
    audit = SecurityAuditService(session)
    await audit.record(
        action="broadcast",
        actor_tg_id=actor_tg,
        actor_user_id=_actor_id(data),
        after={"sent": sent, "failed": failed, "blocked": blocked, "total": total},
    )

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"📨 Отправлено: {sent}\n"
        f"🚫 Заблокировали: {blocked}\n"
        f"❌ Ошибок: {failed}\n"
        f"👥 Всего: {total}",
        reply_markup=_admin_dashboard_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:broadcast_cancel")
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext, **data) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Рассылка отменена.", reply_markup=_admin_dashboard_kb())
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  ЛОГИ
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:logs")
async def cb_admin_logs(callback: CallbackQuery, session: AsyncSession, **data) -> None:
    if not has_permission(data.get("effective_role", UserRole.USER), Permission.VIEW_AUDIT):
        await callback.answer("🚫 Нет прав", show_alert=True)
        return

    from sqlalchemy import select
    from metabot.models.audit import AuditLog
    stmt = (
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    )
    result = await session.execute(stmt)
    logs = result.scalars().all()

    if not logs:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        b = InlineKeyboardBuilder()
        b.button(text="← Назад", callback_data="admin:menu")
        await callback.message.edit_text(
            "📋 Логов пока нет.", reply_markup=b.as_markup()
        )
        await callback.answer()
        return

    lines = ["📋 <b>Аудит (последние 20)</b>\n"]
    for log in logs:
        dt = log.created_at.strftime("%d.%m %H:%M") if log.created_at else "?"
        actor = f"tg:{log.actor_tg_id}" if log.actor_tg_id else "system"
        note = f" — {log.note}" if log.note else ""
        lines.append(f"  {dt} [{actor}] <b>{log.action}</b>{note}")

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(text="← Назад", callback_data="admin:menu")
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=b.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  OSINT ИСТОЧНИКИ
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:osint_sources")
async def cb_osint_sources(callback: CallbackQuery, session: AsyncSession, **data) -> None:
    if not has_permission(data.get("effective_role", UserRole.USER), Permission.MANAGE_SOURCES):
        await callback.answer("🚫 Нет прав", show_alert=True)
        return

    repo = OsintSourceRepository(session)
    sources = await repo.get_all_enabled()

    lines = ["🔧 <b>OSINT Источники</b>\n"]
    if sources:
        for s in sources:
            status = "✅" if s.is_enabled else "❌"
            premium = "💎" if s.is_premium else ""
            lines.append(
                f"  {status}{premium} [{s.category}] <b>{s.name}</b> ({s.source_type})"
            )
    else:
        lines.append("  Нет активных источников.\n")

    lines.append("\n/add_source — добавить источник")

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(text="← Назад", callback_data="admin:menu")
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=b.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.message(Command("add_source"))
async def cmd_add_source(message: Message, state: FSMContext, **data) -> None:
    if not has_permission(data.get("effective_role", UserRole.USER), Permission.MANAGE_SOURCES):
        await message.answer("🚫 Доступ запрещён.")
        return

    await message.answer(
        "🔧 <b>Добавление OSINT-источника</b>\n\nВведите название:",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.add_source_name)


@router.message(AdminStates.add_source_name)
async def process_source_name(message: Message, state: FSMContext, **data) -> None:
    if not _can_admin(data):
        return
    await state.update_data(source_name=sanitize(message.text, 128))
    await message.answer("Slug (уникальный ID, например: my_api_source):")
    await state.set_state(AdminStates.add_source_slug)


@router.message(AdminStates.add_source_slug)
async def process_source_slug(message: Message, state: FSMContext, **data) -> None:
    if not _can_admin(data):
        return
    await state.update_data(source_slug=message.text.strip().lower()[:64])
    await message.answer("Тип: api / scraper / whois / dns / custom")
    await state.set_state(AdminStates.add_source_type)


@router.message(AdminStates.add_source_type)
async def process_source_type(message: Message, state: FSMContext, **data) -> None:
    if not _can_admin(data):
        return
    await state.update_data(source_type=message.text.strip().lower()[:32])
    await message.answer("Категория: phone / email / username / nick / fullname / domain / ip / face")
    await state.set_state(AdminStates.add_source_category)


@router.message(AdminStates.add_source_category)
async def process_source_category(message: Message, state: FSMContext, **data) -> None:
    if not _can_admin(data):
        return
    await state.update_data(source_category=message.text.strip().lower()[:32])
    await message.answer(
        "Конфигурация (JSON) или «-» без конфигурации:\n\n"
        "<code>{\"url\": \"...\", \"headers\": {...}}</code>",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.add_source_config)


@router.message(AdminStates.add_source_config)
async def process_source_config(
    message: Message, state: FSMContext, session: AsyncSession, **data
) -> None:
    if not _can_admin(data):
        return

    fsm_data = await state.get_data()
    await state.clear()

    config = None
    if message.text.strip() != "-":
        try:
            config = json.loads(message.text.strip())
        except json.JSONDecodeError:
            await message.answer(
                "❌ Ошибка парсинга JSON. Источник не создан.",
                reply_markup=_admin_dashboard_kb(),
            )
            return

    repo = OsintSourceRepository(session)
    source = await repo.create(
        name=fsm_data["source_name"],
        slug=fsm_data["source_slug"],
        source_type=fsm_data["source_type"],
        category=fsm_data["source_category"],
        config=config,
        is_enabled=True,
    )

    audit = SecurityAuditService(session)
    await audit.record(
        action="add_osint_source",
        actor_tg_id=_actor_tg_id(data),
        actor_user_id=_actor_id(data),
        after={"name": source.name, "slug": source.slug, "category": source.category},
    )

    await message.answer(
        f"✅ Источник <b>{source.name}</b> добавлен!\n"
        f"  Slug: {source.slug} | Тип: {source.source_type} | Категория: {source.category}",
        reply_markup=_admin_dashboard_kb(),
        parse_mode="HTML",
    )


# ═══════════════════════════════════════════════════════════
#  ТАРИФЫ
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "admin:plans")
async def cb_admin_plans(callback: CallbackQuery, session: AsyncSession, **data) -> None:
    if not _can_admin(data):
        await callback.answer("🚫", show_alert=True)
        return

    sub_service = SubscriptionService(session)
    plans = await sub_service.get_plans()

    lines = ["⚙️ <b>Тарифные планы</b>\n"]
    for p in plans:
        lines.append(
            f"  {'✅' if p.is_active else '❌'} <b>{p.name}</b> ({p.slug})\n"
            f"      ${p.price_usd} / {p.duration_days}д / {p.daily_request_limit} запр./день\n"
            f"      OSINT: {'✅' if p.osint_enabled else '❌'}"
        )

    if not plans:
        lines.append("  Тарифов нет.")

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(text="← Назад", callback_data="admin:menu")
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=b.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  noop (для кнопки-счётчика пагинации)
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
