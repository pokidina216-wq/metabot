"""
Vexis — OSINT Tool Center (v3).

UX-поток:
  1. Пользователь выбирает КАТЕГОРИЮ (телефон, email, домен, IP, ник, ФИО, лицо)
  2. Видит СПИСОК СЕРВИСОВ для этой категории (активные + «скоро»)
  3. Нажимает на сервис → вводит запрос → получает результат
  4. Сервисы без API-ключа → «🔜 Скоро появится»

"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import quote_plus

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.configs import get_settings
from metabot.models.user import User
from metabot.utils.validators import validate_osint_query, sanitize

logger = logging.getLogger(__name__)

router = Router(name="osint")


class OsintStates(StatesGroup):
    waiting_query = State()


# ══════════════════════════════════════════════════════════════
#  КАТАЛОГ СЕРВИСОВ
# ══════════════════════════════════════════════════════════════

# Категории
CATEGORIES = [
    ("phone",    "📱", "Телефон"),
    ("email",    "📧", "Email"),
    ("domain",   "🌐", "Домен"),
    ("ip",       "🌍", "IP-адрес"),
    ("nick",     "🎭", "Никнейм"),
    ("fullname", "👤", "ФИО / Имя"),
    ("face",     "📸", "Лицо"),
]

# Сервисы внутри каждой категории.
# type:
#   "builtin"  — выполняется прямо в боте (есть OsintSource)
#   "external" — открывается по ссылке (url с {q} / {raw})
#   "api"      — нужен API-ключ (settings_key), без него → «скоро»
#
# Для builtin: engine_source — имя класса источника в движке.
# Для api: settings_key — атрибут settings; если пустой → «скоро».

SERVICES: dict[str, list[dict]] = {
    "phone": [
        {"name": "Truecaller",   "type": "external", "url": "https://www.truecaller.com/search/ru/{raw}"},
        {"name": "GetContact",   "type": "external", "url": "https://getcontact.com/search?phone={q}"},
        {"name": "Epieos",       "type": "external", "url": "https://epieos.com/?q={raw}&t=phone"},
        {"name": "NumVerify",    "type": "api",      "settings_key": "numverify_api_key"},
        {"name": "HLR Lookup",   "type": "api",      "settings_key": "hlr_api_key"},
    ],
    "email": [
        {"name": "Hunter.io",    "type": "api",      "settings_key": "hunter_api_key",
         "engine_source": "email", "hint": "Введите email:\n<code>user@example.com</code>"},
        {"name": "Email MX Check", "type": "builtin", "engine_source": "email",
         "hint": "Введите email:\n<code>user@example.com</code>"},
        {"name": "Epieos",       "type": "external", "url": "https://epieos.com/?q={raw}&t=email"},
        {"name": "Have I Been Pwned", "type": "api", "settings_key": "hibp_api_key"},
    ],
    "domain": [
        {"name": "DNS Lookup",   "type": "builtin",  "engine_source": "domain",
         "hint": "Введите домен:\n<code>example.com</code>"},
        {"name": "WHOIS",        "type": "builtin",  "engine_source": "domain",
         "hint": "Введите домен:\n<code>example.com</code>"},
        {"name": "VirusTotal",   "type": "external", "url": "https://www.virustotal.com/gui/domain/{raw}"},
        {"name": "Shodan",       "type": "external", "url": "https://www.shodan.io/search?query={q}"},
    ],
    "ip": [
        {"name": "IP Info",      "type": "builtin",  "engine_source": "ip",
         "hint": "Введите IP:\n<code>8.8.8.8</code>"},
        {"name": "AbuseIPDB",    "type": "external", "url": "https://www.abuseipdb.com/check/{raw}"},
        {"name": "VirusTotal",   "type": "external", "url": "https://www.virustotal.com/gui/ip-address/{raw}"},
        {"name": "Shodan",       "type": "external", "url": "https://www.shodan.io/host/{raw}"},
    ],
    "nick": [
        {"name": "Username OSINT", "type": "builtin", "engine_source": "username",
         "hint": "Введите ник:\n<code>cooluser42</code>"},
        {"name": "WhatsMyName",  "type": "external", "url": "https://whatsmyname.app/?q={raw}"},
        {"name": "Namecheckr",   "type": "external", "url": "https://www.namecheckr.com/?q={raw}"},
        {"name": "Sherlock",     "type": "api",      "settings_key": "sherlock_api_key"},
    ],
    "fullname": [
        {"name": "Google",       "type": "external", "url": 'https://www.google.com/search?q="{q}"'},
        {"name": "Yandex",       "type": "external", "url": "https://yandex.ru/people?q={q}"},
        {"name": "Pipl",         "type": "api",      "settings_key": "pipl_api_key"},
        {"name": "Spokeo",       "type": "api",      "settings_key": "spokeo_api_key"},
    ],
    "face": [
        {"name": "PimEyes",      "type": "external", "url": "https://pimeyes.com/en"},
        {"name": "Search4Faces", "type": "external", "url": "https://search4faces.com/"},
        {"name": "FaceCheck.ID", "type": "external", "url": "https://facecheck.id/"},
    ],
}

# Подсказки для ввода по категориям (для builtin/api сервисов)
CATEGORY_INPUT_HINTS = {
    "phone":    "Введите номер:\n<code>+79001234567</code>",
    "email":    "Введите email:\n<code>user@example.com</code>",
    "domain":   "Введите домен:\n<code>example.com</code>",
    "ip":       "Введите IP:\n<code>8.8.8.8</code>",
    "nick":     "Введите ник:\n<code>cooluser42</code>",
    "fullname": "Введите имя:\n<code>Иванов Иван</code>",
}

# Маппинг категория OSINT → категория движка
_ENGINE_CATEGORIES = {
    "phone": "phone", "email": "email", "domain": "domain",
    "ip": "ip", "nick": "username", "fullname": "fullname",
}

# Emoji для типов сервисов
_SVC_EMOJI = {
    "builtin":  "🟢",  # работает
    "external": "🔗",  # открыть в браузере
    "api":      "",     # будет динамически: 🟢 или 🔜
}


def _service_available(svc: dict) -> bool:
    """True если сервис доступен (builtin/external или API-ключ задан)."""
    if svc["type"] in ("builtin", "external"):
        return True
    if svc["type"] == "api":
        key = svc.get("settings_key", "")
        if not key:
            return False
        settings = get_settings()
        val = getattr(settings, key, "")
        return bool(val)
    return False


# ══════════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════

def osint_categories_kb() -> InlineKeyboardMarkup:
    """Главное меню OSINT — список категорий."""
    rows = []
    row = []
    for cat_id, emoji, label in CATEGORIES:
        row.append(InlineKeyboardButton(
            text=f"{emoji} {label}",
            callback_data=f"osint:cat:{cat_id}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🏠 На главную", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _services_kb(category: str) -> InlineKeyboardMarkup:
    """Список сервисов для категории с индикаторами статуса."""
    svcs = SERVICES.get(category, [])
    rows = []

    for i, svc in enumerate(svcs):
        available = _service_available(svc)

        if svc["type"] == "external":
            # Внешние — кнопка-ссылка (без callback)
            emoji = "🔗"
            rows.append([InlineKeyboardButton(
                text=f"{emoji} {svc['name']}  ↗",
                url=svc["url"].replace("{q}", "...").replace("{raw}", "..."),
            )])
        elif available:
            # Рабочий сервис → запрос данных
            emoji = "🟢"
            rows.append([InlineKeyboardButton(
                text=f"{emoji} {svc['name']}",
                callback_data=f"osint:svc:{category}:{i}",
            )])
        else:
            # Не подключён → «скоро»
            emoji = "🔜"
            rows.append([InlineKeyboardButton(
                text=f"{emoji} {svc['name']} — скоро",
                callback_data=f"osint:soon:{i}",
            )])

    rows.append([
        InlineKeyboardButton(text="← Назад", callback_data="osint:back"),
        InlineKeyboardButton(text="🏠 На главную", callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _result_kb(category: str, query_raw: str) -> InlineKeyboardMarkup:
    """Клавиатура после результатов: повторить, назад."""
    rows = [
        [
            InlineKeyboardButton(text="🔄 Повторить", callback_data=f"osint:cat:{category}"),
            InlineKeyboardButton(text="← Категории", callback_data="osint:back"),
        ],
        [InlineKeyboardButton(text="🏠 На главную", callback_data="nav:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ══════════════════════════════════════════════════════════════
#  ДВИЖОК (lazy singleton)
# ══════════════════════════════════════════════════════════════

_engine = None

def _get_engine():
    global _engine
    if _engine is None:
        from metabot.osint.engine import OsintEngine
        from metabot.osint.sources import (
            DnsSource, WhoisSource, IpInfoSource,
            EmailBreachSource, UsernameOsintSource,
        )
        _engine = OsintEngine()
        _engine.register(DnsSource())
        _engine.register(WhoisSource())
        _engine.register(IpInfoSource())
        _engine.register(EmailBreachSource())
        _engine.register(UsernameOsintSource())
    return _engine


# ══════════════════════════════════════════════════════════════
#  ХЕНДЛЕРЫ
# ══════════════════════════════════════════════════════════════

# ── Главное меню OSINT ─────────────────────────────────────

@router.callback_query(F.data.in_({"menu:osint", "osint:back"}))
async def cb_osint_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "🔎 <b>OSINT — Центр инструментов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите категорию данных для проверки:",
        parse_mode="HTML",
        reply_markup=osint_categories_kb(),
    )
    await callback.answer()


# ── Выбор категории → список сервисов ──────────────────────

@router.callback_query(F.data.startswith("osint:cat:"))
async def cb_osint_category(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    category = callback.data.split(":", 2)[2]

    # Найти emoji и название
    cat_info = next((c for c in CATEGORIES if c[0] == category), None)
    if not cat_info:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    _, emoji, label = cat_info
    svcs = SERVICES.get(category, [])
    active = sum(1 for s in svcs if _service_available(s))
    total = len(svcs)

    text = (
        f"{emoji} <b>{label.upper()}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Доступно сервисов: <b>{active}/{total}</b>\n\n"
        f"🟢 — работает прямо в боте\n"
        f"🔗 — откроется в браузере\n"
        f"🔜 — скоро появится\n\n"
        f"Выберите сервис:"
    )
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_services_kb(category),
    )
    await callback.answer()


# ── «Скоро появится» ────────────────────────────────────────

@router.callback_query(F.data.startswith("osint:soon:"))
async def cb_osint_soon(callback: CallbackQuery) -> None:
    await callback.answer(
        "🔜 Этот сервис скоро появится! Мы работаем над подключением.",
        show_alert=True,
    )


# ── Выбор конкретного сервиса → ввод запроса ────────────────

@router.callback_query(F.data.startswith("osint:svc:"))
async def cb_osint_service(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":", 3)  # osint:svc:category:index
    category = parts[2]
    svc_idx = int(parts[3])

    svcs = SERVICES.get(category, [])
    if svc_idx >= len(svcs):
        await callback.answer("Ошибка", show_alert=True)
        return

    svc = svcs[svc_idx]
    hint = svc.get("hint") or CATEGORY_INPUT_HINTS.get(category, "Введите запрос:")

    cat_info = next((c for c in CATEGORIES if c[0] == category), None)
    emoji = cat_info[1] if cat_info else "🔍"

    await state.update_data(osint_category=category, osint_svc_idx=svc_idx)
    await state.set_state(OsintStates.waiting_query)

    await callback.message.edit_text(
        f"{emoji} <b>{svc['name']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{hint}\n\n"
        f"<i>✏️ Напишите запрос в чат:</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"osint:cat:{category}"),
        ]]),
    )
    await callback.answer()


# ── Обработка введённого запроса ────────────────────────────

@router.message(OsintStates.waiting_query)
async def process_osint_query(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    **data,
) -> None:
    fsm_data = await state.get_data()
    category = fsm_data.get("osint_category", "")
    svc_idx = fsm_data.get("osint_svc_idx", 0)
    raw_query = (message.text or "").strip()
    await state.clear()

    if not raw_query:
        await message.answer("⚠️ Пустой запрос.", reply_markup=osint_categories_kb())
        return

    # ── Валидация ───────────────────────────────────────────
    validated, error_msg = validate_osint_query(category, raw_query)
    if validated is None:
        await message.answer(
            error_msg or "⚠️ Неверный формат запроса.",
            reply_markup=osint_categories_kb(),
            parse_mode="HTML",
        )
        return

    # ── Лимит запросов ──────────────────────────────────────
    db_user: User | None = data.get("db_user")
    if db_user:
        from metabot.services.user_service import UserService
        user_svc = UserService(session)
        allowed, remaining = await user_svc.check_and_increment(db_user)
        if not allowed:
            await message.answer(
                "⚠️ Дневной лимит запросов исчерпан.\n"
                "Оформите подписку для увеличения лимита.",
                reply_markup=osint_categories_kb(),
            )
            return

    # ── Информация о сервисе ────────────────────────────────
    svcs = SERVICES.get(category, [])
    svc = svcs[svc_idx] if svc_idx < len(svcs) else None
    svc_name = svc["name"] if svc else "OSINT"

    cat_info = next((c for c in CATEGORIES if c[0] == category), None)
    emoji = cat_info[1] if cat_info else "🔍"

    # ── Отправляем «загрузку» ───────────────────────────────
    loading_msg = await message.answer(
        f"{emoji} <b>{svc_name}</b>\n\n"
        f"🔍 Запрос: <code>{sanitize(validated, 60)}</code>\n"
        f"⏳ Анализ...",
        parse_mode="HTML",
    )

    # ── Запуск OSINT Engine ─────────────────────────────────
    engine = _get_engine()
    engine_category = _ENGINE_CATEGORIES.get(category, category)

    # Проверяем подписку для premium-источников
    include_premium = False
    if db_user:
        from metabot.services.user_service import UserService
        user_svc = UserService(session)
        plan_name = await user_svc.get_user_plan_name(db_user)
        if plan_name != "Free":
            include_premium = True

    start_time = time.monotonic()
    findings = await engine.search(
        category=engine_category,
        query=validated,
        session=session,
        include_premium=include_premium,
    )
    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    # ── Форматируем результат ───────────────────────────────
    found_count = sum(1 for f in findings if f.found)
    total_sources = len(findings)

    result_lines = [
        f"{emoji} <b>{svc_name}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔍 Запрос: <code>{sanitize(validated, 60)}</code>",
        f"📊 Источников: {found_count}/{total_sources} с данными",
        f"⏱ Время: {elapsed_ms}мс\n",
    ]

    for finding in findings:
        result_lines.append(finding.format_text())
        result_lines.append("")

    result_text = "\n".join(result_lines)

    # Обрезаем если слишком длинный (Telegram лимит 4096)
    if len(result_text) > 4000:
        result_text = result_text[:3950] + "\n\n<i>... результат обрезан</i>"

    try:
        await loading_msg.edit_text(
            result_text,
            parse_mode="HTML",
            reply_markup=_result_kb(category, validated),
            disable_web_page_preview=True,
        )
    except Exception:
        await message.answer(
            result_text,
            parse_mode="HTML",
            reply_markup=_result_kb(category, validated),
            disable_web_page_preview=True,
        )

    # ── Логируем запрос ─────────────────────────────────────
    if db_user:
        from metabot.repositories.request_log_repo import RequestLogRepository
        log_repo = RequestLogRepository(session)
        await log_repo.create(
            user_id=db_user.id,
            action=f"osint_{category}_{svc_name}",
            details=f"query={validated[:100]}",
            processing_time_ms=elapsed_ms,
        )
