"""
Vexis — OSINT-хендлер.

Для большинства категорий (телефон, email, домен, IP, ФИО, лицо) отдаём
готовые ссылки на проверенные публичные ресурсы.

Категория «По нику» (`nick`) ведёт себя ИНАЧЕ (audit-fix):
- Принимаем никнейм от пользователя.
- ЗАПУСКАЕМ реальный поиск по сетке платформ (см. UsernameOsintSource).
- Возвращаем СПИСОК РЕАЛЬНО НАЙДЕННЫХ ССЫЛОК или сообщение
  «❌ Ничего не найдено». Никаких «найдено на сайте X», «вероятно
  относится к…» и прочих предположений.
"""
from __future__ import annotations

import logging
from typing import List
from urllib.parse import quote_plus

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.models.user import User
from metabot.osint import get_osint_engine
from metabot.repositories.request_log_repo import RequestLogRepository
from metabot.services.user_service import UserService

logger = logging.getLogger(__name__)

router = Router(name="osint")


class OsintStates(StatesGroup):
    waiting_query = State()


# ─── Ссылки по категориям (для категорий БЕЗ собственного движка) ──────────
# {q} — URL-encoded; {raw} — как есть.
OSINT_LINKS: dict[str, list[dict]] = {
    "phone": [
        {"name": "See-Know",              "url": "https://see-know.eu/"},
        {"name": "Intelligence Security", "url": "https://intelligencesecurity.io/ru/"},
        {"name": "Epieos",                "url": "https://epieos.com/?q={raw}&t=phone"},
        {"name": "GetContact",            "url": "https://getcontact.com/search?phone={q}"},
        {"name": "Truecaller",            "url": "https://www.truecaller.com/search/ru/{raw}"},
    ],
    "email": [
        {"name": "Epieos",        "url": "https://epieos.com/?q={raw}&t=email"},
        {"name": "FTH.so",        "url": "https://fth.so/"},
        {"name": "BigHack.me",    "url": "https://bighack.me/"},
        {"name": "Bonday.xyz",    "url": "https://bonday.xyz/"},
        {"name": "VipChecker",    "url": "https://vipchecker.ru/"},
        {"name": "RusFinder",     "url": "https://rusfinder.pro/"},
    ],
    "domain": [
        {"name": "Whois (DomainTools)", "url": "https://whois.domaintools.com/{raw}"},
        {"name": "Whois (ICANN)",       "url": "https://lookup.icann.org/en/lookup?name={raw}"},
        {"name": "VirusTotal",          "url": "https://www.virustotal.com/gui/domain/{raw}"},
        {"name": "DNSDumpster",         "url": "https://dnsdumpster.com/?q={raw}"},
        {"name": "Shodan",              "url": "https://www.shodan.io/search?query={q}"},
    ],
    "ip": [
        {"name": "IPInfo",       "url": "https://ipinfo.io/{raw}"},
        {"name": "AbuseIPDB",    "url": "https://www.abuseipdb.com/check/{raw}"},
        {"name": "VirusTotal",   "url": "https://www.virustotal.com/gui/ip-address/{raw}"},
        {"name": "Shodan",       "url": "https://www.shodan.io/host/{raw}"},
        {"name": "IPVoid",       "url": "https://www.ipvoid.com/ip-blacklist-check/?ip={raw}"},
    ],
    # «По нику» (nick) сюда не входит — он обслуживается реальным
    # движком поиска. См. _run_nick_search ниже.
    "fullname": [
        {"name": "Yandex People",     "url": "https://yandex.ru/people?q={q}"},
        {"name": "VK Поиск",          "url": "https://vk.com/search?c[q]={q}&c[section]=people"},
        {"name": "Google",            "url": "https://www.google.com/search?q=%22{q}%22"},
        {"name": "Maltego (Guide)",   "url": "https://www.maltego.com/maltego-community/"},
        {"name": "Pipl (Guide)",      "url": "https://pipl.com/search/?q={q}"},
    ],
    "face": [
        {"name": "PimEyes",      "url": "https://pimeyes.com/en"},
        {"name": "Search4Faces", "url": "https://search4faces.com/"},
        {"name": "Yandex Images","url": "https://yandex.ru/images/"},
        {"name": "TinEye",       "url": "https://tineye.com/"},
        {"name": "FaceCheck.ID", "url": "https://facecheck.id/"},
    ],
}

CATEGORY_UI = {
    "phone":    ("📱", "ПО НОМЕРУ",  "Введите номер телефона:\nПример: <code>+79001234567</code>"),
    "email":    ("📧", "ПО EMAIL",   "Введите email-адрес:\nПример: <code>user@example.com</code>"),
    "domain":   ("🌐", "ПО ДОМЕНУ",  "Введите домен:\nПример: <code>example.com</code>"),
    "ip":       ("🌍", "ПО IP",      "Введите IP-адрес:\nПример: <code>8.8.8.8</code>"),
    "nick":     ("🎭", "ПО НИКУ",    "Введите никнейм:\nПример: <code>cooluser42</code>"),
    "fullname": ("👤", "ПО ФИО",     "Введите имя (или ФИО):\nПример: <code>Иванов Иван</code>"),
    "face":     ("📸", "ПО ЛИЦУ",    None),
}


def _build_links_keyboard(category: str, query_raw: str) -> InlineKeyboardMarkup:
    """Inline-клавиатура со ссылками (для категорий БЕЗ движка)."""
    links = OSINT_LINKS.get(category, [])
    q = quote_plus(query_raw)
    buttons = []
    for item in links:
        url = item["url"].replace("{q}", q).replace("{raw}", query_raw)
        buttons.append([InlineKeyboardButton(text=f"🔗 {item['name']}", url=url)])
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="osint:back"),
        InlineKeyboardButton(text="🏠 Главная", callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _nick_result_kb() -> InlineKeyboardMarkup:
    """Клавиатура после реального поиска по нику."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Новый поиск", callback_data="osint:nick")],
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data="osint:back"),
            InlineKeyboardButton(text="🏠 Главная", callback_data="nav:home"),
        ],
    ])


def _osint_menu_kb() -> InlineKeyboardMarkup:
    """Меню выбора категории OSINT."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 По номеру",  callback_data="osint:phone"),
            InlineKeyboardButton(text="📧 По email",   callback_data="osint:email"),
        ],
        [
            InlineKeyboardButton(text="🌐 По домену",  callback_data="osint:domain"),
            InlineKeyboardButton(text="🌍 По IP",      callback_data="osint:ip"),
        ],
        [
            InlineKeyboardButton(text="🎭 По нику",    callback_data="osint:nick"),
            InlineKeyboardButton(text="👤 По ФИО",     callback_data="osint:fullname"),
        ],
        [
            InlineKeyboardButton(text="📸 По лицу",    callback_data="osint:face"),
        ],
        [
            InlineKeyboardButton(text="🏠 На главную", callback_data="nav:home"),
        ],
    ])


_OSINT_MENU_TEXT = (
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "  🔎 <b>OSINT ЦЕНТР</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Выберите категорию для проверки:"
)


@router.callback_query(F.data == "osint:back")
async def cb_osint_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.edit_text(
            _OSINT_MENU_TEXT, parse_mode="HTML", reply_markup=_osint_menu_kb(),
        )
    except Exception:  # noqa: BLE001 — сообщение могло устареть
        await callback.message.answer(
            _OSINT_MENU_TEXT, parse_mode="HTML", reply_markup=_osint_menu_kb(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("osint:"))
async def cb_osint_category(callback: CallbackQuery, state: FSMContext) -> None:
    """Раскрываем форму ввода или сразу показываем ссылки для категории «лицо»."""
    category = callback.data.split(":", 1)[1]
    if category == "back":
        # обработано выше
        return
    ui = CATEGORY_UI.get(category)
    if not ui:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    emoji, title, hint = ui

    # Лицо — сразу ссылки без ввода (поиск возможен только загрузкой фото
    # напрямую на внешний сервис).
    if category == "face":
        links = OSINT_LINKS.get("face", [])
        buttons = []
        for item in links:
            buttons.append([InlineKeyboardButton(text=f"🔗 {item['name']}", url=item["url"])])
        buttons.append([
            InlineKeyboardButton(text="◀️ Назад", callback_data="osint:back"),
            InlineKeyboardButton(text="🏠 Главная", callback_data="nav:home"),
        ])
        await callback.message.edit_text(
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "  📸 <b>ПОИСК ПО ЛИЦУ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Загрузи фото напрямую на один из сервисов:\n",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()
        return

    await state.update_data(osint_category=category)
    await state.set_state(OsintStates.waiting_query)
    await callback.message.edit_text(
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {emoji} <b>ПРОВЕРКА {title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{hint}\n\n"
        f"<i>✏️ Напишите запрос в чат:</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отмена", callback_data="osint:back"),
        ]]),
    )
    await callback.answer()


async def _run_nick_search(
    message: Message,
    query: str,
    session: AsyncSession,
    db_user: User,
) -> None:
    """Реальный поиск по нику: список найденных URL или «Ничего не найдено»."""
    # Лимиты: списываем 1 запрос ровно ОДИН раз и только при валидном вводе.
    user_service = UserService(session)
    allowed, remaining = await user_service.check_and_increment(db_user)
    if not allowed:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "  ⚠️ <b>ЛИМИТ ИСЧЕРПАН</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Дневной лимит запросов исчерпан.\n"
            "💎 Обновите тариф в «💎 Подписка».",
            parse_mode="HTML",
            reply_markup=_nick_result_kb(),
        )
        return

    wait_msg = await message.answer(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🎭 <b>ПОИСК ПО НИКУ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔍 Запрос: <code>{query}</code>\n"
        "⏳ Проверяю платформы...",
        parse_mode="HTML",
    )

    engine = get_osint_engine()
    findings = await engine.search(category="nick", query=query, session=session)

    # Собираем ТОЛЬКО реально найденные URL (с дедупликацией порядка).
    seen = set()
    urls: List[str] = []
    for f in findings:
        if f.error:
            # Ошибки конкретных источников НЕ показываем пользователю —
            # это не «найдено», и не нужно засорять выдачу.
            continue
        for u in f.urls:
            if u not in seen:
                seen.add(u)
                urls.append(u)

    # Лог запроса
    try:
        log_repo = RequestLogRepository(session)
        await log_repo.create(
            user_id=db_user.id,
            action="osint_query",
            details=f"category=nick query={query} found={len(urls)}",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to log osint nick search: %s", e)

    if not urls:
        text = (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "  🎭 <b>ПОИСК ПО НИКУ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔍 Запрос: <code>{query}</code>\n\n"
            "❌ <b>Ничего не найдено.</b>\n\n"
            f"📊 Осталось запросов: {remaining}"
        )
    else:
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━",
            "  🎭 <b>ПОИСК ПО НИКУ</b>",
            "━━━━━━━━━━━━━━━━━━━━━━\n",
            f"🔍 Запрос: <code>{query}</code>",
            f"✅ <b>Найдено ({len(urls)}):</b>",
            "",
        ]
        # Выводим как plain-URL — Telegram автоматически кликабельны.
        for url in urls:
            lines.append(f"• {url}")
        lines.append("")
        lines.append(f"📊 Осталось запросов: {remaining}")
        text = "\n".join(lines)

    await wait_msg.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_nick_result_kb(),
        disable_web_page_preview=True,
    )


@router.message(OsintStates.waiting_query)
async def process_osint_query(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
) -> None:
    data = await state.get_data()
    category = data.get("osint_category", "")
    query = message.text.strip() if message.text else ""
    await state.clear()

    if not query:
        await message.answer("⚠️ Пустой запрос. Попробуйте снова.",
                             reply_markup=_osint_menu_kb())
        return

    # «По нику»: реальный поиск, ТОЛЬКО реально найденные URL.
    if category == "nick":
        await _run_nick_search(message, query, session, db_user)
        return

    # Остальные категории пока работают через готовые ссылки на агрегаторы.
    ui = CATEGORY_UI.get(category, ("🔍", "ПРОВЕРКА", ""))
    emoji, title, _ = ui
    links = OSINT_LINKS.get(category, [])
    count = len(links)

    await message.answer(
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {emoji} <b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔍 Запрос: <code>{query}</code>\n"
        f"📋 Сервисов для проверки: <b>{count}</b>\n\n"
        f"Нажми на сервис, чтобы открыть проверку 👇",
        parse_mode="HTML",
        reply_markup=_build_links_keyboard(category, query),
        disable_web_page_preview=True,
    )
