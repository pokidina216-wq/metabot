"""
Vexis — обработка Reply-кнопок главного меню + inline-навигация.
Все разделы: проверка данных, метаданные, username, профиль,
подписка, рефералы, настройки, помощь.
"""
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.keyboards import (
    main_menu_kb,
    metadata_waiting_kb,
    osint_menu_kb,
    username_length_kb,
    profile_kb,
    subscription_kb,
    referral_kb,
    settings_kb,
    language_kb,
    help_kb,
    help_back_kb,
)
from metabot.models.user import User
from metabot.utils.validators import sanitize

router = Router(name="menu")


# ═══════════════════════════════════════════════════════════
#  НАВИГАЦИЯ — «На главную» (Reply + Inline)
# ═══════════════════════════════════════════════════════════

VEXIS_HOME = (
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "  🔮 <b>VEXIS</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Выберите действие 👇"
)


@router.message(F.text.in_({"🏠 На главную", "🏠 Главное меню", "/menu"}))
async def go_home_reply(message: Message) -> None:
    await message.answer(VEXIS_HOME, reply_markup=main_menu_kb(), parse_mode="HTML")


@router.callback_query(F.data == "nav:home")
async def go_home_inline(callback: CallbackQuery) -> None:
    await callback.message.answer(
        VEXIS_HOME, reply_markup=main_menu_kb(), parse_mode="HTML"
    )
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  ПРОВЕРКА ДАННЫХ (OSINT) — Reply-кнопка → inline-меню в osint_handler
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "🔍 Проверка данных")
async def menu_osint(message: Message) -> None:
    text = (
        "🔎 <b>OSINT — Центр инструментов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите тип проверки.\n"
        "Встроенные инструменты дадут результат прямо здесь.\n"
        "Внешние источники — дополнительно по ссылкам."
    )
    await message.answer(text, reply_markup=osint_menu_kb(), parse_mode="HTML")

# ВАЖНО: callback_query «menu:osint» обрабатывается в osint_handler.py
# (единая точка — без дублирования)


# ═══════════════════════════════════════════════════════════
#  МЕТАДАННЫЕ
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "📂 Метаданные")
async def menu_metadata(message: Message) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  📂 <b>АНАЛИЗ МЕТАДАННЫХ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Отправьте мне файл как <b>документ</b> 📎\n"
        "и я мгновенно извлеку все метаданные.\n\n"
        "<b>Поддерживаемые форматы:</b>\n"
        "┣ 📷 Изображения — JPEG, PNG, TIFF, HEIC\n"
        "┣ 🎬 Видео — MP4, AVI, MKV, MOV\n"
        "┣ 🎵 Аудио — MP3, FLAC, WAV, OGG\n"
        "┣ 📄 Документы — PDF, DOCX, XLSX, PPTX\n"
        "┗ 📦 Архивы — ZIP, TAR, 7Z\n\n"
        "⚠️ <i>Важно: отправляйте как «Файл», не как «Фото» —\n"
        "иначе Telegram удалит все метаданные.</i>"
    )
    await message.answer(text, reply_markup=metadata_waiting_kb(), parse_mode="HTML")


@router.callback_query(F.data == "menu:metadata")
async def cb_menu_metadata(callback: CallbackQuery) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  📂 <b>АНАЛИЗ МЕТАДАННЫХ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Отправьте файл как <b>документ</b> 📎\n\n"
        "⚠️ <i>Именно как «Файл», не как «Фото»!</i>"
    )
    await callback.message.answer(text, reply_markup=metadata_waiting_kb(), parse_mode="HTML")
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  USERNAME FINDER
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "🔤 Username Finder")
async def menu_username(message: Message) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔤 <b>USERNAME FINDER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Найдём свободные Telegram @username.\n\n"
        "<b>Шаг 1 из 3</b> — Выберите длину:"
    )
    await message.answer(text, reply_markup=username_length_kb(), parse_mode="HTML")


@router.callback_query(F.data == "menu:username")
async def cb_menu_username(callback: CallbackQuery) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔤 <b>USERNAME FINDER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Найдём свободные Telegram @username.\n\n"
        "<b>Шаг 1 из 3</b> — Выберите длину:"
    )
    await callback.message.edit_text(text, reply_markup=username_length_kb(), parse_mode="HTML")
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  ПРОФИЛЬ
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "👤 Профиль")
async def menu_profile(message: Message, db_user: User, session: AsyncSession) -> None:
    text = await _build_profile_text(db_user, session)
    await message.answer(text, reply_markup=profile_kb(), parse_mode="HTML")


@router.callback_query(F.data == "menu:profile")
async def cb_menu_profile(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    text = await _build_profile_text(db_user, session)
    await callback.message.edit_text(text, reply_markup=profile_kb(), parse_mode="HTML")
    await callback.answer()


async def _build_profile_text(db_user: User, session: AsyncSession) -> str:
    from metabot.services.user_service import UserService
    user_service = UserService(session)
    plan_name = await user_service.get_user_plan_name(db_user)
    daily_limit = await user_service.get_daily_limit(db_user)
    safe_uname = sanitize(db_user.username, 32) if db_user.username else "—"

    return (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  👤 <b>ПРОФИЛЬ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"┣ 🆔 <b>ID:</b> <code>{db_user.telegram_id}</code>\n"
        f"┣ 📛 <b>Username:</b> @{safe_uname}\n"
        f"┣ 📅 <b>Регистрация:</b> {db_user.created_at.strftime('%d.%m.%Y')}\n"
        f"┣ 📊 <b>Тариф:</b> {sanitize(plan_name, 32)}\n"
        f"┣ 📈 <b>Сегодня:</b> {db_user.daily_requests_used}/{daily_limit}\n"
        f"┣ 📊 <b>Всего:</b> {db_user.total_requests} запросов\n"
        f"┗ 🎁 <b>Бонусы:</b> {db_user.referral_bonus_balance}"
    )


# ═══════════════════════════════════════════════════════════
#  ПОДПИСКА
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "💎 Подписка")
async def menu_subscription(message: Message, db_user: User, session: AsyncSession) -> None:
    text = await _build_subscription_text(db_user, session)
    from metabot.services.subscription_service import SubscriptionService
    sub = await SubscriptionService(session).get_active_sub(db_user.id)
    await message.answer(
        text, reply_markup=subscription_kb(has_active=sub is not None), parse_mode="HTML"
    )


@router.callback_query(F.data == "menu:subscription")
async def cb_menu_subscription(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    text = await _build_subscription_text(db_user, session)
    from metabot.services.subscription_service import SubscriptionService
    sub = await SubscriptionService(session).get_active_sub(db_user.id)
    await callback.message.edit_text(
        text, reply_markup=subscription_kb(has_active=sub is not None), parse_mode="HTML"
    )
    await callback.answer()


async def _build_subscription_text(db_user: User, session: AsyncSession) -> str:
    from metabot.services.subscription_service import SubscriptionService
    sub_service = SubscriptionService(session)
    active_sub = await sub_service.get_active_sub(db_user.id)

    header = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  💎 <b>ПОДПИСКА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    if active_sub:
        return header + (
            f"✅ <b>Активна</b>\n\n"
            f"┣ 📦 <b>Тариф:</b> {active_sub.plan.name}\n"
            f"┣ 📅 <b>До:</b> {active_sub.expires_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"┗ 📊 <b>Лимит:</b> {active_sub.plan.daily_request_limit} запросов/день"
        )
    return header + (
        "❌ <b>Нет активной подписки</b>\n\n"
        "Текущий тариф: <b>Free</b> (5 запросов/день)\n\n"
        "💎 Выберите «Тарифы» для обновления!"
    )


# ═══════════════════════════════════════════════════════════
#  РЕФЕРАЛЫ
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "👥 Рефералы")
async def menu_referrals(message: Message, db_user: User, session: AsyncSession) -> None:
    text = await _build_referral_text(db_user, session)
    await message.answer(text, reply_markup=referral_kb(), parse_mode="HTML")


@router.callback_query(F.data == "menu:referrals")
async def cb_menu_referrals(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    text = await _build_referral_text(db_user, session)
    await callback.message.edit_text(text, reply_markup=referral_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "profile:referrals")
async def cb_profile_referrals(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    text = await _build_referral_text(db_user, session)
    await callback.message.edit_text(text, reply_markup=referral_kb(), parse_mode="HTML")
    await callback.answer()


async def _build_referral_text(db_user: User, session: AsyncSession) -> str:
    from metabot.services.referral_service import ReferralService
    from metabot.configs import get_settings
    settings = get_settings()
    ref_service = ReferralService(session, settings.bot_username)
    stats = await ref_service.get_stats(db_user)

    return (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  👥 <b>РЕФЕРАЛЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 <b>Ваша ссылка:</b>\n"
        f"<code>{stats.referral_link}</code>\n\n"
        f"┣ 👤 <b>Приглашено:</b> {stats.total_referrals}\n"
        f"┣ ✅ <b>Активировано:</b> {stats.activated}\n"
        f"┗ 🎁 <b>Бонусов:</b> {stats.total_bonuses}\n\n"
        f"<b>Как получать бонусы:</b>\n"
        f"┣ +1 запрос — за каждую регистрацию\n"
        f"┗ +5 запросов — за покупку подписки рефералом"
    )


# ═══════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "⚙️ Настройки")
async def menu_settings(message: Message, db_user: User) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  ⚙️ <b>НАСТРОЙКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔔 Уведомления: {'✅ Включены' if db_user.notifications_enabled else '❌ Выключены'}\n"
        f"🌐 Язык: {db_user.language_code or 'Авто'}"
    )
    await message.answer(
        text,
        reply_markup=settings_kb(db_user.notifications_enabled),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "menu:settings")
async def cb_menu_settings(callback: CallbackQuery, db_user: User) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  ⚙️ <b>НАСТРОЙКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔔 Уведомления: {'✅ Включены' if db_user.notifications_enabled else '❌ Выключены'}\n"
        f"🌐 Язык: {db_user.language_code or 'Авто'}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=settings_kb(db_user.notifications_enabled),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "settings:toggle_notif")
async def cb_toggle_notifications(
    callback: CallbackQuery, db_user: User, session: AsyncSession
) -> None:
    from metabot.services.user_service import UserService
    user_service = UserService(session)
    new_state = not db_user.notifications_enabled
    await user_service.update_notifications(db_user, new_state)

    emoji = "✅ Включены" if new_state else "❌ Выключены"
    await callback.message.edit_text(
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  ⚙️ <b>НАСТРОЙКИ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔔 Уведомления: {emoji}\n"
        f"🌐 Язык: {db_user.language_code or 'Авто'}",
        reply_markup=settings_kb(new_state),
        parse_mode="HTML",
    )
    await callback.answer(f"Уведомления {'включены' if new_state else 'выключены'}")


@router.callback_query(F.data == "settings:language")
async def cb_language(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🌐 <b>Выберите язык:</b>",
        reply_markup=language_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lang:"))
async def cb_set_language(
    callback: CallbackQuery, db_user: User, session: AsyncSession
) -> None:
    lang = callback.data.split(":", 1)[1]
    from metabot.services.user_service import UserService
    user_service = UserService(session)
    await user_service.update_language(db_user, lang)

    lang_label = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}.get(lang, lang)
    await callback.message.edit_text(
        f"✅ Язык изменён на: {lang_label}",
        reply_markup=settings_kb(db_user.notifications_enabled),
        parse_mode="HTML",
    )
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  ПОМОЩЬ
# ═══════════════════════════════════════════════════════════

@router.message(F.text == "❓ Помощь")
async def menu_help(message: Message) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  ❓ <b>ПОМОЩЬ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите тему:"
    )
    await message.answer(text, reply_markup=help_kb(), parse_mode="HTML")


@router.callback_query(F.data == "menu:help")
async def cb_menu_help(callback: CallbackQuery) -> None:
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  ❓ <b>ПОМОЩЬ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите тему:"
    )
    await callback.message.edit_text(text, reply_markup=help_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "help:files")
async def cb_help_files(callback: CallbackQuery) -> None:
    text = (
        "📂 <b>Как отправлять файлы</b>\n\n"
        "1️⃣ Нажмите 📎 (скрепка) в поле ввода\n"
        "2️⃣ Выберите <b>«Файл»</b>, а не «Фото»\n"
        "3️⃣ Выберите файл и отправьте\n\n"
        "⚠️ Если отправить фото через «Фото», Telegram\n"
        "удалит все метаданные (EXIF, GPS и т.д.).\n\n"
        "✅ Только отправка как «Файл»/«Документ»\n"
        "сохраняет оригинальные метаданные."
    )
    await callback.message.edit_text(text, reply_markup=help_back_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "help:osint")
async def cb_help_osint(callback: CallbackQuery) -> None:
    text = (
        "🔍 <b>Что такое OSINT?</b>\n\n"
        "OSINT (Open Source Intelligence) — поиск\n"
        "информации по открытым источникам.\n\n"
        "Vexis ищет по:\n"
        "┣ 📱 Номерам телефонов\n"
        "┣ 📧 Email-адресам\n"
        "┣ 🌐 Доменам и IP\n"
        "┣ 🎭 Никнеймам и ФИО\n"
        "┗ 📸 Фотографиям лиц\n\n"
        "Мы используем только легальные API\n"
        "и открытые базы данных."
    )
    await callback.message.edit_text(text, reply_markup=help_back_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "help:subs")
async def cb_help_subs(callback: CallbackQuery) -> None:
    from metabot.configs import get_settings
    settings = get_settings()
    text = (
        "💎 <b>О подписках</b>\n\n"
        "🆓 <b>Free</b> — 5 запросов/день\n"
        "💎 <b>Premium</b> — 50 запросов/день + OSINT\n"
        "👑 <b>VIP</b> — 200 запросов/день + все инструменты\n\n"
        "<b>Как оформить:</b>\n"
        "1️⃣ Откройте «💎 Подписка» → «📋 Тарифы»\n"
        "2️⃣ Выберите план → «📨 Оставить заявку»\n"
        "3️⃣ Владелец рассмотрит и активирует подписку\n\n"
        f"По вопросам оплаты: @{settings.support_contact}"
    )
    await callback.message.edit_text(text, reply_markup=help_back_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "help:referrals")
async def cb_help_referrals(callback: CallbackQuery) -> None:
    text = (
        "👥 <b>О реферальной программе</b>\n\n"
        "1️⃣ Скопируйте ссылку в разделе «Рефералы»\n"
        "2️⃣ Поделитесь с друзьями\n"
        "3️⃣ Получайте бонусы:\n\n"
        "┣ <b>+1 запрос</b> — за регистрацию друга\n"
        "┗ <b>+5 запросов</b> — за покупку подписки"
    )
    await callback.message.edit_text(text, reply_markup=help_back_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "help:support")
async def cb_help_support(callback: CallbackQuery) -> None:
    from metabot.configs import get_settings
    settings = get_settings()
    text = (
        "💬 <b>Поддержка</b>\n\n"
        "Если у вас есть вопросы или проблемы,\n"
        "свяжитесь с нами:\n\n"
        f"📩 @{settings.support_contact}\n\n"
        "🔒 Ваша безопасность — наш приоритет."
    )
    await callback.message.edit_text(text, reply_markup=help_back_kb(), parse_mode="HTML")
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  ПРОФИЛЬ — Inline подразделы
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data == "profile:subscription")
async def cb_profile_sub(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    """Переход к подписке из профиля."""
    text = await _build_subscription_text(db_user, session)
    from metabot.services.subscription_service import SubscriptionService
    sub = await SubscriptionService(session).get_active_sub(db_user.id)
    await callback.message.edit_text(
        text, reply_markup=subscription_kb(has_active=sub is not None), parse_mode="HTML"
    )
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  «СКОПИРОВАТЬ» — отправка текста для копирования
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("copy:ref_link"))
async def cb_copy_ref_link(callback: CallbackQuery, db_user: User, session: AsyncSession) -> None:
    from metabot.services.referral_service import ReferralService
    from metabot.configs import get_settings
    settings = get_settings()
    ref_service = ReferralService(session, settings.bot_username)
    stats = await ref_service.get_stats(db_user)
    await callback.message.answer(
        f"<code>{stats.referral_link}</code>\n\n"
        f"👆 Нажмите на ссылку, чтобы скопировать",
        parse_mode="HTML",
    )
    await callback.answer("Ссылка ниже 👇")


@router.callback_query(F.data.startswith("copy:"))
async def cb_copy_generic(callback: CallbackQuery) -> None:
    """Копирование: повторная отправка текста как <code> для лёгкого копирования."""
    # Отправляем последний текст сообщения заново с тегом <code>
    original = callback.message.text or callback.message.html_text or ""
    # Убираем HTML теги для чистого текста
    import re
    clean = re.sub(r"<[^>]+>", "", original)
    if len(clean) > 4000:
        clean = clean[:4000]
    await callback.message.answer(
        f"<code>{clean}</code>\n\n👆 Нажмите для копирования",
        parse_mode="HTML",
    )
    await callback.answer("Текст ниже 👇")
