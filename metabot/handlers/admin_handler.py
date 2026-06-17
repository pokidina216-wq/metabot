"""
Админ-панель — полное управление ботом через Telegram.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.configs import get_settings
from metabot.keyboards import admin_menu_kb, pagination_kb, confirm_kb, nav_home_kb
from metabot.models.user import User, UserRole
from metabot.repositories.admin_repo import AdminActionRepository
from metabot.repositories.user_repo import UserRepository
from metabot.repositories.osint_repo import OsintSourceRepository
from metabot.services.analytics_service import AnalyticsService
from metabot.services.subscription_service import SubscriptionService
from metabot.services.security_service import SecurityAuditService, OwnerProtectionError

logger = logging.getLogger(__name__)

router = Router(name="admin")


# ── Фильтр доступа ────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    settings = get_settings()
    # Владелец всегда имеет доступ к админ-панели.
    if settings.owner_id and user_id == settings.owner_id:
        return True
    return user_id in settings.admin_id_list


class AdminStates(StatesGroup):
    search_query = State()
    broadcast_text = State()
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


# ── Вход в админ-панель ───────────────────────────────────
@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("🚫 Доступ запрещён.")
        return

    await message.answer(
        "🛠 <b>Админ-панель</b>\n\nВыберите раздел:",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )


# ── Аналитика ─────────────────────────────────────────────
@router.callback_query(F.data == "admin:analytics")
async def cb_analytics(callback: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    analytics = AnalyticsService(session)
    stats = await analytics.get_dashboard()

    text = (
        "📊 <b>Аналитика</b>\n\n"
        f"👥 <b>Пользователи:</b>\n"
        f"  • Всего: {stats.total_users:,}\n"
        f"  • Новых сегодня: {stats.new_users_today}\n"
        f"  • Новых за неделю: {stats.new_users_week}\n"
        f"  • Активных сегодня: {stats.active_today}\n"
        f"  • Активных за неделю: {stats.active_week}\n\n"
        f"📈 <b>Запросы:</b>\n"
        f"  • Сегодня: {stats.requests_today:,}\n"
        f"  • За неделю: {stats.requests_week:,}\n\n"
        f"💰 <b>Доход:</b>\n"
        f"  • Сегодня: ${stats.revenue_today:.2f}\n"
        f"  • За неделю: ${stats.revenue_week:.2f}\n\n"
    )

    if stats.popular_tools:
        text += "🔧 <b>Популярные инструменты (неделя):</b>\n"
        for action, cnt in stats.popular_tools:
            text += f"  • {action}: {cnt}\n"

    await callback.message.edit_text(
        text, reply_markup=admin_menu_kb(), parse_mode="HTML"
    )
    await callback.answer()


# ── Список пользователей ──────────────────────────────────
@router.callback_query(F.data == "admin:users")
async def cb_users_list(callback: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    repo = UserRepository(session)
    total = await repo.count()
    users = await repo.get_many(offset=0, limit=15, order_by=User.created_at.desc())

    lines = [f"👥 <b>Пользователи</b> (всего: {total:,})\n"]
    for u in users:
        ban_icon = "🚫" if u.is_banned else ""
        role_icon = {"owner": "👑", "admin": "🔑", "superadmin": "👑", "premium": "💎", "moderator": "🛡"}.get(
            u.role.value, ""
        )
        lines.append(
            f"  {role_icon}{ban_icon} <code>{u.telegram_id}</code> — "
            f"@{u.username or 'N/A'} ({u.total_requests} запр.)"
        )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Поиск пользователей ───────────────────────────────────
@router.callback_query(F.data == "admin:search")
async def cb_admin_search(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await callback.message.edit_text(
        "🔍 Введите username, имя или Telegram ID для поиска:"
    )
    await state.set_state(AdminStates.search_query)
    await callback.answer()


@router.message(AdminStates.search_query)
async def process_admin_search(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.clear()

    query = message.text.strip()
    repo = UserRepository(session)
    users = await repo.search(query)

    if not users:
        await message.answer(
            f"🔍 По запросу «{query}» ничего не найдено.",
            reply_markup=admin_menu_kb(),
        )
        return

    lines = [f"🔍 <b>Результаты поиска:</b> «{query}»\n"]
    for u in users:
        sub_text = f"{'💎' if u.role == UserRole.PREMIUM else '🆓'}"
        lines.append(
            f"  {sub_text} <code>{u.telegram_id}</code> — "
            f"@{u.username or 'N/A'} | {u.first_name or ''}\n"
            f"      Запросов: {u.total_requests} | Бан: {'Да' if u.is_banned else 'Нет'}"
        )

    await message.answer("\n".join(lines), reply_markup=admin_menu_kb(), parse_mode="HTML")


# ── Управление подписками (ручная выдача) ──────────────────
@router.callback_query(F.data == "admin:subscriptions")
async def cb_admin_subs(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await callback.message.edit_text(
        "💎 <b>Выдача подписки</b>\n\n"
        "Введите Telegram ID пользователя:",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.grant_sub_user_id)
    await callback.answer()


@router.message(AdminStates.grant_sub_user_id)
async def process_grant_user_id(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    try:
        tg_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введите числовой Telegram ID:")
        return

    await state.update_data(grant_tg_id=tg_id)
    await message.answer(
        "Введите slug тарифа (premium / vip):"
    )
    await state.set_state(AdminStates.grant_sub_plan)


@router.message(AdminStates.grant_sub_plan)
async def process_grant_plan(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not is_admin(message.from_user.id):
        return

    plan_slug = message.text.strip().lower()
    data = await state.get_data()
    tg_id = data["grant_tg_id"]
    await state.clear()

    repo = UserRepository(session)
    user = await repo.get_by_telegram_id(tg_id)
    if not user:
        await message.answer(f"❌ Пользователь {tg_id} не найден.", reply_markup=admin_menu_kb())
        return

    sub_service = SubscriptionService(session)
    plan = await sub_service.get_plan_by_slug(plan_slug)
    if not plan:
        await message.answer(f"❌ Тариф «{plan_slug}» не найден.", reply_markup=admin_menu_kb())
        return

    sub = await sub_service.activate_subscription(
        user=user, plan=plan, payment_method="admin_grant"
    )

    # Аудит
    action_repo = AdminActionRepository(session)
    await action_repo.create(
        admin_user_id=None,
        action="grant_subscription",
        target_user_id=user.id,
        details=f"plan={plan_slug} until={sub.expires_at.isoformat()}",
    )

    await message.answer(
        f"✅ Подписка <b>{plan.name}</b> выдана пользователю <code>{tg_id}</code>\n"
        f"Активна до: {sub.expires_at.strftime('%d.%m.%Y %H:%M')}",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )


# ── Бан / Разбан ──────────────────────────────────────────
@router.callback_query(F.data == "admin:ban")
async def cb_admin_ban(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await callback.message.edit_text("🚫 Введите Telegram ID для бана:")
    await state.set_state(AdminStates.ban_user_id)
    await callback.answer()


@router.message(AdminStates.ban_user_id)
async def process_ban_id(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
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
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not is_admin(message.from_user.id):
        return

    reason = message.text.strip()
    if reason == "-":
        reason = None

    data = await state.get_data()
    tg_id = data["ban_tg_id"]
    await state.clear()

    repo = UserRepository(session)
    user = await repo.get_by_telegram_id(tg_id)
    if not user:
        await message.answer(f"❌ Пользователь {tg_id} не найден.", reply_markup=admin_menu_kb())
        return

    # Защита Owner: нельзя банить владельца (код + триггер БД = defense in depth)
    settings = get_settings()
    audit = SecurityAuditService(session)
    try:
        await audit.assert_not_owner_target(
            user, settings.owner_id, op="ban", actor_tg_id=message.from_user.id
        )
    except OwnerProtectionError:
        await session.commit()
        await message.answer(
            "🛡 Этого пользователя нельзя забанить — это владелец системы.",
            reply_markup=admin_menu_kb(),
        )
        return

    before = {"is_banned": user.is_banned, "ban_reason": user.ban_reason}
    await repo.ban_user(user.id, reason)

    # Аудит: append-only журнал + legacy admin_actions
    await audit.record(
        action="ban_user",
        actor_tg_id=message.from_user.id,
        target_user_id=user.id,
        target_tg_id=user.telegram_id,
        target_type="user",
        before=before,
        after={"is_banned": True, "ban_reason": reason},
        note=f"reason={reason}",
    )
    action_repo = AdminActionRepository(session)
    await action_repo.create(
        admin_user_id=None,
        action="ban_user",
        target_user_id=user.id,
        details=f"reason={reason}",
    )
    await session.commit()

    await message.answer(
        f"🚫 Пользователь <code>{tg_id}</code> забанен.\nПричина: {reason or 'N/A'}",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )


# ── Рассылка ──────────────────────────────────────────────
@router.callback_query(F.data == "admin:broadcast")
async def cb_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\n"
        "Введите текст сообщения для рассылки всем пользователям.\n"
        "Поддерживается HTML-разметка.\n\n"
        "Отправьте «отмена» для отмены.",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.broadcast_text)
    await callback.answer()


@router.message(AdminStates.broadcast_text)
async def process_broadcast(
    message: Message,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
) -> None:
    if not is_admin(message.from_user.id):
        return

    text = message.text.strip()
    if text.lower() == "отмена":
        await state.clear()
        await message.answer("❌ Рассылка отменена.", reply_markup=admin_menu_kb())
        return

    await state.clear()

    repo = UserRepository(session)
    total = await repo.count()

    status_msg = await message.answer(f"📢 Начинаю рассылку для {total} пользователей...")

    sent, failed = 0, 0
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
            except Exception:
                failed += 1

        offset += batch_size

        # Обновляем статус
        if offset % 500 == 0:
            await status_msg.edit_text(
                f"📢 Рассылка... Отправлено: {sent}, Ошибок: {failed}"
            )

    # Аудит
    action_repo = AdminActionRepository(session)
    await action_repo.create(
        admin_user_id=None,
        action="broadcast",
        details=f"sent={sent} failed={failed} total={total}",
    )

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"📨 Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}\n"
        f"👥 Всего: {total}",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )


# ── Логи ───────────────────────────────────────────────────
@router.callback_query(F.data == "admin:logs")
async def cb_admin_logs(callback: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    action_repo = AdminActionRepository(session)
    actions = await action_repo.get_recent(limit=20)

    if not actions:
        await callback.message.edit_text(
            "📋 Логов пока нет.", reply_markup=admin_menu_kb()
        )
        await callback.answer()
        return

    lines = ["📋 <b>Последние действия админов:</b>\n"]
    for a in actions:
        dt = a.created_at.strftime("%d.%m %H:%M")
        lines.append(f"  {dt} — {a.action}: {a.details or 'N/A'}")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=admin_menu_kb(), parse_mode="HTML"
    )
    await callback.answer()


# ── Управление OSINT-источниками ───────────────────────────
@router.callback_query(F.data == "admin:osint_sources")
async def cb_osint_sources(callback: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    repo = OsintSourceRepository(session)
    sources = await repo.get_all_enabled()

    lines = ["🔧 <b>OSINT Источники:</b>\n"]
    if sources:
        for s in sources:
            status = "✅" if s.is_enabled else "❌"
            premium = "💎" if s.is_premium else ""
            lines.append(
                f"  {status}{premium} [{s.category}] <b>{s.name}</b> ({s.source_type})"
            )
    else:
        lines.append("  Нет активных источников.\n")

    lines.append("\nДля добавления источника используйте команду:")
    lines.append("/add_source")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=admin_menu_kb(), parse_mode="HTML"
    )
    await callback.answer()


@router.message(Command("add_source"))
async def cmd_add_source(message: Message, state: FSMContext) -> None:
    """Добавление нового OSINT-источника через диалог."""
    if not is_admin(message.from_user.id):
        await message.answer("🚫 Доступ запрещён.")
        return

    await message.answer(
        "🔧 <b>Добавление OSINT-источника</b>\n\n"
        "Введите название источника:",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.add_source_name)


@router.message(AdminStates.add_source_name)
async def process_source_name(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(source_name=message.text.strip())
    await message.answer("Введите slug (уникальный ID, например: my_api_source):")
    await state.set_state(AdminStates.add_source_slug)


@router.message(AdminStates.add_source_slug)
async def process_source_slug(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(source_slug=message.text.strip().lower())
    await message.answer(
        "Введите тип источника:\n"
        "api / scraper / whois / dns / custom"
    )
    await state.set_state(AdminStates.add_source_type)


@router.message(AdminStates.add_source_type)
async def process_source_type(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(source_type=message.text.strip().lower())
    await message.answer(
        "Введите категорию:\n"
        "phone / email / username / nick / fullname / domain / ip / face"
    )
    await state.set_state(AdminStates.add_source_category)


@router.message(AdminStates.add_source_category)
async def process_source_category(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(source_category=message.text.strip().lower())
    await message.answer(
        "Введите конфигурацию в формате JSON:\n\n"
        "<code>{\n"
        '  "url": "https://api.example.com/lookup",\n'
        '  "method": "GET",\n'
        '  "headers": {"X-Api-Key": "YOUR_KEY"},\n'
        '  "params_template": {"q": "{query}"},\n'
        '  "response_path": "data.results"\n'
        "}</code>\n\n"
        "Или отправьте «-» для пустой конфигурации.",
        parse_mode="HTML",
    )
    await state.set_state(AdminStates.add_source_config)


@router.message(AdminStates.add_source_config)
async def process_source_config(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    await state.clear()

    config = None
    if message.text.strip() != "-":
        import json
        try:
            config = json.loads(message.text.strip())
        except json.JSONDecodeError:
            await message.answer(
                "❌ Ошибка парсинга JSON. Источник не создан.",
                reply_markup=admin_menu_kb(),
            )
            return

    repo = OsintSourceRepository(session)
    source = await repo.create(
        name=data["source_name"],
        slug=data["source_slug"],
        source_type=data["source_type"],
        category=data["source_category"],
        config=config,
        is_enabled=True,
    )

    action_repo = AdminActionRepository(session)
    await action_repo.create(
        admin_user_id=None,
        action="add_osint_source",
        details=f"name={source.name} slug={source.slug} category={source.category}",
    )

    await message.answer(
        f"✅ Источник <b>{source.name}</b> добавлен!\n\n"
        f"  • Slug: {source.slug}\n"
        f"  • Тип: {source.source_type}\n"
        f"  • Категория: {source.category}\n"
        f"  • Конфиг: {'✅' if config else '❌'}\n",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )


# ── Управление тарифами ───────────────────────────────────
@router.callback_query(F.data == "admin:plans")
async def cb_admin_plans(callback: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    sub_service = SubscriptionService(session)
    plans = await sub_service.get_plans()

    lines = ["⚙️ <b>Тарифные планы:</b>\n"]
    for p in plans:
        lines.append(
            f"  {'✅' if p.is_active else '❌'} <b>{p.name}</b> ({p.slug})\n"
            f"      ${p.price_usd} / {p.duration_days}д / {p.daily_request_limit} запр.\n"
            f"      OSINT: {'✅' if p.osint_enabled else '❌'}"
        )

    if not plans:
        lines.append("  Тарифов нет. Создайте через SQL или скрипт инициализации.")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=admin_menu_kb(), parse_mode="HTML"
    )
    await callback.answer()


# ── Рефералы (админ) ──────────────────────────────────────
@router.callback_query(F.data == "admin:referrals")
async def cb_admin_referrals(callback: CallbackQuery, session: AsyncSession) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫", show_alert=True)
        return

    from metabot.repositories.referral_repo import ReferralRepository
    repo = ReferralRepository(session)
    total = await repo.count()

    await callback.message.edit_text(
        f"👥 <b>Реферальная система</b>\n\n"
        f"📊 Всего реферальных связей: {total}\n",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )
    await callback.answer()
