"""
Vexis — OSINT-хендлер.
Принимает ввод от пользователя → отдаёт готовые ссылки с запросом.
"""
from __future__ import annotations

import logging
from urllib.parse import quote_plus

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

logger = logging.getLogger(__name__)

router = Router(name="osint")


class OsintStates(StatesGroup):
    waiting_query = State()


# ─── Ссылки по категориям ───────────────────────────────────────────────────
# {q} — подставляется запрос пользователя (URL-encoded)
# {raw} — подставляется как есть (без кодирования)

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
    "nick": [
        {"name": "WhatsMyName",  "url": "https://whatsmyname.app/?q={raw}"},
        {"name": "Namecheckr",   "url": "https://www.namecheckr.com/?q={raw}"},
        {"name": "Sherlock",     "url": "https://sherlock-project.github.io/?q={raw}"},
        {"name": "Maigret",      "url": "https://github.com/soxoj/maigret"},
        {"name": "Usersearch.org","url": "https://usersearch.org/results_normal.php?q={q}"},
    ],
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
    """Строит inline-клавиатуру со ссылками."""
    links = OSINT_LINKS.get(category, [])
    q = quote_plus(query_raw)
    buttons = []
    for item in links:
        url = item["url"].replace("{q}", q).replace("{raw}", query_raw)
        buttons.append([InlineKeyboardButton(text=f"🔗 {item['name']}", url=url)])
    # Кнопки навигации
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="osint:back"),
        InlineKeyboardButton(text="🏠 Главная", callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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


@router.callback_query(F.data == "osint:back")
async def cb_osint_back(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔎 <b>OSINT ЦЕНТР</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите категорию для проверки:",
        parse_mode="HTML",
        reply_markup=_osint_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:osint")
async def cb_osint_open(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔎 <b>OSINT ЦЕНТР</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите категорию для проверки:",
        parse_mode="HTML",
        reply_markup=_osint_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("osint:"))
async def cb_osint_category(callback: CallbackQuery, state: FSMContext) -> None:
    category = callback.data.split(":", 1)[1]
    ui = CATEGORY_UI.get(category)
    if not ui:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    emoji, title, hint = ui

    # Лицо — сразу ссылки без ввода
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


@router.message(OsintStates.waiting_query)
async def process_osint_query(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    category = data.get("osint_category", "")
    query = message.text.strip() if message.text else ""
    await state.clear()

    if not query:
        await message.answer("⚠️ Пустой запрос. Попробуйте снова.", reply_markup=_osint_menu_kb())
        return

    ui = CATEGORY_UI.get(category, ("🔍", "ПРОВЕРКА", ""))
    emoji, title, _ = ui

    links = OSINT_LINKS.get(category, [])
    count = len(links)

    await message.answer(
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {emoji} <b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔍 Запрос: <code>{query}</code>\n"
        f"📋 Найдено источников: <b>{count}</b>\n\n"
        f"Нажми на сайт для проверки 👇",
        parse_mode="HTML",
        reply_markup=_build_links_keyboard(category, query),
        disable_web_page_preview=True,
    )
