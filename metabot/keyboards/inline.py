"""
Vexis — Inline-клавиатуры для всех разделов.
Полная кнопочная навигация: «Назад», «На главную», «Повторить», «Скопировать».
"""
from __future__ import annotations

from typing import Optional, Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from metabot.models.subscription import Plan


# ═══════════════════════════════════════════════════════════
#  НАВИГАЦИЯ (общие)
# ═══════════════════════════════════════════════════════════

def _nav_row(builder: InlineKeyboardBuilder, back_cb: str | None = None, home: bool = True) -> None:
    """Добавить ряд навигации: ← Назад / 🏠 На главную."""
    if back_cb:
        builder.button(text="← Назад", callback_data=back_cb)
    if home:
        builder.button(text="🏠 На главную", callback_data="nav:home")


def nav_home_kb() -> InlineKeyboardMarkup:
    """Только кнопка «На главную»."""
    b = InlineKeyboardBuilder()
    b.button(text="🏠 На главную", callback_data="nav:home")
    b.adjust(1)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  ПРОВЕРКА ДАННЫХ (OSINT)
# ═══════════════════════════════════════════════════════════

def osint_menu_kb() -> InlineKeyboardMarkup:
    """Подменю «Проверка данных» — 7 категорий + навигация."""
    b = InlineKeyboardBuilder()
    b.button(text="📱 По номеру",  callback_data="osint:phone")
    b.button(text="📧 По почте",   callback_data="osint:email")
    b.button(text="🌐 По домену",  callback_data="osint:domain")
    b.button(text="🌍 По IP",      callback_data="osint:ip")
    b.button(text="🎭 По нику",    callback_data="osint:nick")
    b.button(text="👤 По ФИО",     callback_data="osint:fullname")
    b.button(text="📸 По лицу",    callback_data="osint:face")
    # навигация
    b.button(text="🏠 На главную",  callback_data="nav:home")
    b.adjust(2, 2, 2, 1, 1)
    return b.as_markup()


def osint_result_kb(category: str, query: str) -> InlineKeyboardMarkup:
    """Кнопки после результата OSINT: повторить, скопировать, назад, домой."""
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Повторить поиск",    callback_data=f"osint:{category}")
    b.button(text="📋 Скопировать результат", callback_data=f"copy:osint:{category}:{query[:60]}")
    b.button(text="← Назад",               callback_data="menu:osint")
    b.button(text="🏠 На главную",          callback_data="nav:home")
    b.adjust(1, 1, 2)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  МЕТАДАННЫЕ
# ═══════════════════════════════════════════════════════════

def metadata_result_kb(has_report: bool = True) -> InlineKeyboardMarkup:
    """Кнопки после анализа файла."""
    b = InlineKeyboardBuilder()
    if has_report:
        b.button(text="📥 Скачать сводку (TXT)", callback_data="dl:meta:txt")
        b.button(text="🧾 Скачать JSON", callback_data="dl:meta:json")
    b.button(text="📂 Отправить ещё файл", callback_data="menu:metadata")
    b.button(text="🏠 На главную", callback_data="nav:home")
    if has_report:
        b.adjust(2, 1, 1)
    else:
        b.adjust(1, 1)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  USERNAME FINDER — пошаговый мастер
# ═══════════════════════════════════════════════════════════

def username_length_kb() -> InlineKeyboardMarkup:
    """Шаг 1 — выбор длины username."""
    b = InlineKeyboardBuilder()
    for length in [5, 6, 7, 8, 10, 12, 15]:
        b.button(text=str(length), callback_data=f"ulen:{length}")
    b.button(text="✏️ Своя длина", callback_data="ulen:custom")
    # навигация
    b.button(text="🏠 На главную", callback_data="nav:home")
    b.adjust(4, 3, 1, 1)
    return b.as_markup()


def username_count_kb() -> InlineKeyboardMarkup:
    """Шаг 2 — количество вариантов."""
    b = InlineKeyboardBuilder()
    for cnt in [5, 10, 15, 20, 30, 50]:
        b.button(text=str(cnt), callback_data=f"ucnt:{cnt}")
    # навигация
    b.button(text="← Назад", callback_data="menu:username")
    b.button(text="🏠 На главную", callback_data="nav:home")
    b.adjust(3, 3, 2)
    return b.as_markup()


def username_type_kb() -> InlineKeyboardMarkup:
    """Шаг 3 — тип username."""
    b = InlineKeyboardBuilder()
    b.button(text="🔤 Только буквы",      callback_data="utype:letters_only")
    b.button(text="🔢 Буквы + цифры",     callback_data="utype:letters_digits")
    b.button(text="✨ Красивые",           callback_data="utype:beautiful")
    b.button(text="🏢 Брендовые",          callback_data="utype:brand")
    b.button(text="🔁 Повторяющиеся",      callback_data="utype:repeating")
    # навигация
    b.button(text="← Назад", callback_data="ustep:back_count")
    b.button(text="🏠 На главную", callback_data="nav:home")
    b.adjust(2, 2, 1, 2)
    return b.as_markup()


def username_result_kb() -> InlineKeyboardMarkup:
    """Кнопки после результатов username."""
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Новый поиск",        callback_data="menu:username")
    b.button(text="🔍 Проверить конкретный", callback_data="uname:check_one")
    b.button(text="📋 Скопировать результат", callback_data="copy:username")
    b.button(text="🏠 На главную",          callback_data="nav:home")
    b.adjust(1, 1, 1, 1)
    return b.as_markup()


def username_check_kb() -> InlineKeyboardMarkup:
    """Проверка конкретного username — кнопки."""
    b = InlineKeyboardBuilder()
    b.button(text="🔍 Проверить ещё", callback_data="uname:check_one")
    b.button(text="← Назад",          callback_data="menu:username")
    b.button(text="🏠 На главную",    callback_data="nav:home")
    b.adjust(1, 2)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  ПРОФИЛЬ
# ═══════════════════════════════════════════════════════════

def profile_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 История запросов",  callback_data="profile:history")
    b.button(text="💎 Моя подписка",       callback_data="profile:subscription")
    b.button(text="👥 Мои рефералы",       callback_data="profile:referrals")
    b.button(text="🏠 На главную",         callback_data="nav:home")
    b.adjust(1, 2, 1)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  ПОДПИСКА
# ═══════════════════════════════════════════════════════════

def subscription_kb(has_active: bool = False) -> InlineKeyboardMarkup:
    from metabot.configs import get_settings
    settings = get_settings()
    b = InlineKeyboardBuilder()
    b.button(text="📋 Тарифы и цены", callback_data="sub:plans")
    b.button(text="🎟 Ввести промокод", callback_data="promo:enter")
    b.button(
        text=f"💬 Написать @{settings.support_contact}",
        url=f"https://t.me/{settings.support_contact}",
    )
    if has_active:
        b.button(text="📜 История платежей", callback_data="sub:history")
    b.button(text="🏠 На главную", callback_data="nav:home")
    b.adjust(1)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  РЕФЕРАЛЫ
# ═══════════════════════════════════════════════════════════

def referral_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Подробная статистика", callback_data="ref:stats_detail")
    b.button(text="📋 Скопировать ссылку",   callback_data="copy:ref_link")
    b.button(text="🏠 На главную",           callback_data="nav:home")
    b.adjust(1)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════

def settings_kb(notifications_on: bool = True) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    notif_text = "🔕 Выключить уведомления" if notifications_on else "🔔 Включить уведомления"
    b.button(text=notif_text,          callback_data="settings:toggle_notif")
    b.button(text="🌐 Язык",           callback_data="settings:language")
    b.button(text="🏠 На главную",     callback_data="nav:home")
    b.adjust(1)
    return b.as_markup()


def language_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🇷🇺 Русский",  callback_data="lang:ru")
    b.button(text="🇬🇧 English", callback_data="lang:en")
    b.button(text="← Назад",      callback_data="menu:settings")
    b.adjust(2, 1)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  ПОМОЩЬ
# ═══════════════════════════════════════════════════════════

def help_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📂 Как отправлять файлы", callback_data="help:files")
    b.button(text="🔍 Что такое OSINT",      callback_data="help:osint")
    b.button(text="💎 О подписках",           callback_data="help:subs")
    b.button(text="👥 О рефералах",           callback_data="help:referrals")
    b.button(text="💬 Поддержка",             callback_data="help:support")
    b.button(text="🏠 На главную",            callback_data="nav:home")
    b.adjust(1)
    return b.as_markup()


def help_back_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="← Назад", callback_data="menu:help")
    b.button(text="🏠 На главную", callback_data="nav:home")
    b.adjust(2)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  TELEGRAM INFO
# ═══════════════════════════════════════════════════════════

def tg_info_result_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📋 Скопировать результат", callback_data="copy:tg_info")
    b.button(text="🏠 На главную",           callback_data="nav:home")
    b.adjust(1)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  АДМИН-ПАНЕЛЬ
# ═══════════════════════════════════════════════════════════

def admin_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    buttons = [
        ("📊 Аналитика",           "admin:analytics"),
        ("👥 Пользователи",        "admin:users"),
        ("🔍 Поиск",               "admin:search"),
        ("💎 Выдать подписку",      "admin:subscriptions"),
        ("🚫 Бан / Разбан",        "admin:ban"),
        ("📢 Рассылка",            "admin:broadcast"),
        ("🔧 Источники OSINT",     "admin:osint_sources"),
        ("⚙️ Тарифы",             "admin:plans"),
        ("👥 Рефералы",            "admin:referrals"),
        ("📋 Логи",                "admin:logs"),
    ]
    for text, cb in buttons:
        b.button(text=text, callback_data=cb)
    b.button(text="🏠 На главную", callback_data="nav:home")
    b.adjust(2, 2, 2, 2, 1, 1)
    return b.as_markup()


# ═══════════════════════════════════════════════════════════
#  ОБЩИЕ
# ═══════════════════════════════════════════════════════════

def confirm_kb(action: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, подтверждаю", callback_data=f"confirm:{action}")
    b.button(text="❌ Отмена",          callback_data="cancel")
    b.adjust(2)
    return b.as_markup()


def pagination_kb(prefix: str, current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if current_page > 1:
        b.button(text="◀️", callback_data=f"{prefix}:page:{current_page - 1}")
    b.button(text=f"{current_page}/{total_pages}", callback_data="noop")
    if current_page < total_pages:
        b.button(text="▶️", callback_data=f"{prefix}:page:{current_page + 1}")
    b.button(text="← Назад", callback_data=f"{prefix}:back")
    b.button(text="🏠 На главную", callback_data="nav:home")
    b.adjust(3, 2)
    return b.as_markup()
