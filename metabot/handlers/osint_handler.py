"""
Vexis — OSINT Tool Center.

Полная переработка (v2):
- Встроенные источники (DNS, WHOIS, IP, Email MX, Username OSINT) выполняются
  прямо в боте через OsintEngine → реальные результаты отображаются inline.
- Внешние ссылки сохранены как «Ещё источники» после результатов.
- Валидация ввода через validators.py.
- Логирование запросов в request_log.
- Учёт дневного лимита.
"""
from __future__ import annotations

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
from metabot.osint.engine import OsintEngine
from metabot.osint.sources import (
    DnsSource, WhoisSource, IpInfoSource,
    EmailBreachSource, UsernameOsintSource,
)
from metabot.utils.validators import validate_osint_query, sanitize

logger = logging.getLogger(__name__)

router = Router(name="osint")

# ── Singleton движка с зарегистрированными встроенными источниками ──
_engine: OsintEngine | None = None


def _get_engine() -> OsintEngine:
    global _engine
    if _engine is None:
        _engine = OsintEngine()
        _engine.register(DnsSource())
        _engine.register(WhoisSource())
        _engine.register(IpInfoSource())
        _engine.register(EmailBreachSource())
        _engine.register(UsernameOsintSource())
    return _engine


class OsintStates(StatesGroup):
    waiting_query = State()


# ── Внешние ссылки (keep as «Ещё источники») ──────────────────────

OSINT_LINKS: dict[str, list[dict]] = {
    "phone": [
        {"name": "Truecaller",  "url": "https://www.truecaller.com/search/ru/{raw}"},
        {"name": "GetContact",  "url": "https://getcontact.com/search?phone={q}"},
        {"name": "Epieos",      "url": "https://epieos.com/?q={raw}&t=phone"},
    ],
    "email": [
        {"name": "Epieos",    "url": "https://epieos.com/?q={raw}&t=email"},
        {"name": "Hunter.io", "url": "https://hunter.io/email-verifier/{raw}"},
    ],
    "domain": [
        {"name": "VirusTotal", "url": "https://www.virustotal.com/gui/domain/{raw}"},
        {"name": "Shodan",     "url": "https://www.shodan.io/search?query={q}"},
    ],
    "ip": [
        {"name": "AbuseIPDB",  "url": "https://www.abuseipdb.com/check/{raw}"},
        {"name": "VirusTotal", "url": "https://www.virustotal.com/gui/ip-address/{raw}"},
        {"name": "Shodan",     "url": "https://www.shodan.io/host/{raw}"},
    ],
    "nick": [
        {"name": "WhatsMyName", "url": "https://whatsmyname.app/?q={raw}"},
        {"name": "Namecheckr",  "url": "https://www.namecheckr.com/?q={raw}"},
    ],
    "fullname": [
        {"name": "Google",      "url": 'https://www.google.com/search?q="{q}"'},
        {"name": "Yandex",      "url": "https://yandex.ru/people?q={q}"},
    ],
    "face": [
        {"name": "PimEyes",      "url": "https://pimeyes.com/en"},
        {"name": "Search4Faces", "url": "https://search4faces.com/"},
        {"name": "FaceCheck.ID", "url": "https://facecheck.id/"},
    ],
}

CATEGORY_UI = {
    "phone":    ("📱", "НОМЕР ТЕЛЕФОНА",  "Введите номер:\n<code>+79001234567</code>"),
    "email":    ("📧", "EMAIL",           "Введите email:\n<code>user@example.com</code>"),
    "domain":   ("🌐", "ДОМЕН",           "Введите домен:\n<code>example.com</code>"),
    "ip":       ("🌍", "IP-АДРЕС",        "Введите IP:\n<code>8.8.8.8</code>"),
    "nick":     ("🎭", "НИКНЕЙМ",         "Введите ник:\n<code>cooluser42</code>"),
    "fullname": ("👤", "ФИО / ИМЯ",       "Введите имя:\n<code>Иванов Иван</code>"),
    "face":     ("📸", "ЛИЦО",            None),
}

# Маппинг категория OSINT → категория движка
_ENGINE_CATEGORIES = {"phone": "phone", "email": "email", "domain": "domain",
                      "ip": "ip", "nick": "username", "fullname": "fullname"}


# ── Клавиатуры ──────────────────────────────────────────────

def _osint_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 Телефон", callback_data="osint:phone"),
            InlineKeyboardButton(text="📧 Email",   callback_data="osint:email"),
        ],
        [
            InlineKeyboardButton(text="🌐 Домен",   callback_data="osint:domain"),
            InlineKeyboardButton(text="🌍 IP",       callback_data="osint:ip"),
        ],
        [
            InlineKeyboardButton(text="🎭 Никнейм",  callback_data="osint:nick"),
            InlineKeyboardButton(text="👤 ФИО",       callback_data="osint:fullname"),
        ],
        [
            InlineKeyboardButton(text="📸 Лицо",     callback_data="osint:face"),
        ],
        [
            InlineKeyboardButton(text="🏠 На главную", callback_data="nav:home"),
        ],
    ])


def _result_kb(category: str, query_raw: str) -> InlineKeyboardMarkup:
    """Клавиатура после результатов: внешние ссылки + навигация."""
    links = OSINT_LINKS.get(category, [])
    q = quote_plus(query_raw)
    buttons = []

    if links:
        row = []
        for item in links:
            url = item["url"].replace("{q}", q).replace("{raw}", query_raw)
            row.append(InlineKeyboardButton(text=f"🔗 {item['name']}", url=url))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    buttons.append([
        InlineKeyboardButton(text="🔄 Повторить", callback_data=f"osint:{category}"),
        InlineKeyboardButton(text="← Назад",     callback_data="osint:back"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🏠 На главную", callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Хендлеры ──────────────────────────────────────────────

@router.callback_query(F.data.in_({"menu:osint", "osint:back"}))
async def cb_osint_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "🔎 <b>OSINT — Центр инструментов</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите тип проверки.\n"
        "Встроенные инструменты дадут результат прямо здесь.\n"
        "Внешние источники — дополнительно по ссылкам.",
        parse_mode="HTML",
        reply_markup=_osint_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("osint:"))
async def cb_osint_category(callback: CallbackQuery, state: FSMContext) -> None:
    category = callback.data.split(":", 1)[1]
    if category in ("back",):
        return  # Обработано выше

    ui = CATEGORY_UI.get(category)
    if not ui:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    emoji, title, hint = ui

    # Лицо — только ссылки, без ввода
    if category == "face":
        links = OSINT_LINKS.get("face", [])
        buttons = [[InlineKeyboardButton(text=f"🔗 {item['name']}", url=item["url"])]
                   for item in links]
        buttons.append([
            InlineKeyboardButton(text="← Назад", callback_data="osint:back"),
            InlineKeyboardButton(text="🏠 На главную", callback_data="nav:home"),
        ])
        await callback.message.edit_text(
            f"📸 <b>Поиск по лицу</b>\n\n"
            f"Загрузите фото на один из сервисов ниже:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()
        return

    await state.update_data(osint_category=category)
    await state.set_state(OsintStates.waiting_query)
    await callback.message.edit_text(
        f"{emoji} <b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{hint}\n\n"
        f"<i>✏️ Напишите запрос в чат:</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отмена", callback_data="osint:back"),
        ]]),
    )
    await callback.answer()


@router.message(OsintStates.waiting_query)
async def process_osint_query(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    **data,
) -> None:
    fsm_data = await state.get_data()
    category = fsm_data.get("osint_category", "")
    raw_query = (message.text or "").strip()
    await state.clear()

    if not raw_query:
        await message.answer(
            "⚠️ Пустой запрос.",
            reply_markup=_osint_menu_kb(),
        )
        return

    # ── Валидация ───────────────────────────────────────────
    validated, error_msg = validate_osint_query(category, raw_query)
    if validated is None:
        await message.answer(
            error_msg or "⚠️ Неверный формат запроса.",
            reply_markup=_osint_menu_kb(),
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
                reply_markup=_osint_menu_kb(),
            )
            return

    ui = CATEGORY_UI.get(category, ("🔍", "ПРОВЕРКА", ""))
    emoji, title, _ = ui

    # ── Отправляем «загрузку» ───────────────────────────────
    loading_msg = await message.answer(
        f"{emoji} <b>{title}</b>\n\n"
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
        f"{emoji} <b>{title}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔍 Запрос: <code>{sanitize(validated, 60)}</code>",
        f"📊 Источников: {found_count}/{total_sources} с данными",
        f"⏱ Время: {elapsed_ms}мс\n",
    ]

    for finding in findings:
        result_lines.append(finding.format_text())
        result_lines.append("")

    # Подсказка про внешние ссылки
    ext_links = OSINT_LINKS.get(category, [])
    if ext_links:
        result_lines.append(f"🔗 <i>Дополнительно: {len(ext_links)} внешних источников ↓</i>")

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
        # Если edit не сработал — отправляем новое сообщение
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
            action=f"osint_{category}",
            details=f"query={validated[:100]}",
            processing_time_ms=elapsed_ms,
        )
