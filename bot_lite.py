"""
Vexis LITE — режим «одного окна».

Всё происходит в одном сообщении:
- /start → фото + меню-сообщение
- Все разделы редактируют это одно сообщение
- Текстовые ответы пользователя: его сообщение удаляется, окно обновляется
- Документы: его сообщение удаляется, результат показывается в окне

Установка:
    pip install aiogram python-dotenv Pillow mutagen pypdf
             python-docx openpyxl python-pptx aiohttp-socks

Запуск:
    python bot_lite.py
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import string
import random
from itertools import cycle
from pathlib import Path
from urllib.parse import quote_plus

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile,
)
from dotenv import load_dotenv

load_dotenv()

# ─── Логирование ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Конфиг ──────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан в .env!")

_WELCOME_PHOTO = Path(__file__).parent / "welcome.png"

# ─── Прокси ──────────────────────────────────────────────────────────────────
def _load_proxies() -> list[str]:
    p = Path(__file__).parent / "proxies.txt"
    if not p.exists():
        return []
    result = []
    for ln in p.read_text().splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if ln.startswith("socks") or ln.startswith("http"):
            result.append(ln)
        else:
            port = int(ln.split(":")[-1])
            proto = "http" if port in (80, 8080, 3128, 8118, 8888, 3000) else "socks5"
            result.append(f"{proto}://{ln}")
    return result

_PROXIES = _load_proxies()

def _make_session(proxy_url: str | None = None) -> AiohttpSession:
    return AiohttpSession(proxy=proxy_url) if proxy_url else AiohttpSession()

# ─── Хранилище «окна» (user_id → (chat_id, msg_id)) ─────────────────────────
# Единое сообщение-окно на пользователя
_window: dict[int, tuple[int, int]] = {}

async def edit_window(
    bot: Bot,
    user_id: int,
    text: str,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    """Редактирует текущее окно пользователя."""
    entry = _window.get(user_id)
    if not entry:
        return
    chat_id, msg_id = entry
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode="HTML",
            reply_markup=kb,
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.warning("edit_window error: %s", e)

async def delete_user_msg(message: Message) -> None:
    """Удаляет сообщение пользователя (тихо)."""
    try:
        await message.delete()
    except Exception:
        pass

# ─── Роутер ──────────────────────────────────────────────────────────────────
router = Router()

# ─── FSM States ──────────────────────────────────────────────────────────────
class OsintState(StatesGroup):
    waiting = State()

class UsernameState(StatesGroup):
    length = State()
    count  = State()
    type   = State()

class TgInfoState(StatesGroup):
    waiting = State()

class MetaState(StatesGroup):
    waiting = State()

# ─── OSINT данные ─────────────────────────────────────────────────────────────
OSINT_LINKS: dict[str, list[dict]] = {
    "phone": [
        {"name": "See-Know",              "url": "https://see-know.eu/"},
        {"name": "Intelligence Security", "url": "https://intelligencesecurity.io/ru/"},
        {"name": "Epieos",                "url": "https://epieos.com/?q={raw}&t=phone"},
        {"name": "GetContact",            "url": "https://getcontact.com/search?phone={q}"},
        {"name": "Truecaller",            "url": "https://www.truecaller.com/search/ru/{raw}"},
    ],
    "email": [
        {"name": "Epieos",     "url": "https://epieos.com/?q={raw}&t=email"},
        {"name": "FTH.so",     "url": "https://fth.so/"},
        {"name": "BigHack.me", "url": "https://bighack.me/"},
        {"name": "Bonday.xyz", "url": "https://bonday.xyz/"},
        {"name": "VipChecker", "url": "https://vipchecker.ru/"},
        {"name": "RusFinder",  "url": "https://rusfinder.pro/"},
    ],
    "domain":   [],  # скоро
    "ip":       [],  # скоро
    "nick":     [],  # скоро
    "fullname": [],  # скоро
    "face": [
        {"name": "Search4Faces",    "url": "http://search4faces.com/"},
        {"name": "Яндекс Картинки", "url": "https://yandex.ru/images/search"},
        {"name": "BetaFace",        "url": "http://betaface.com/demo_old.html"},
        {"name": "OK Поиск людей",  "url": "https://ok.ru/search?st.mode=Users&st.vpl.mini=fa"},
        {"name": "Online-VK",       "url": "https://online-vk.ru/"},
    ],
}

OSINT_UI = {
    "phone":    ("📱", "ПО НОМЕРУ",  "Введи номер телефона:\n<i>Пример: <code>+79001234567</code></i>"),
    "email":    ("📧", "ПО EMAIL",   "Введи email:\n<i>Пример: <code>user@example.com</code></i>"),
    "domain":   ("🌐", "ПО ДОМЕНУ",  "Введи домен:\n<i>Пример: <code>example.com</code></i>"),
    "ip":       ("🌍", "ПО IP",      "Введи IP-адрес:\n<i>Пример: <code>8.8.8.8</code></i>"),
    "nick":     ("🎭", "ПО НИКУ",    "Введи никнейм:\n<i>Пример: <code>cooluser42</code></i>"),
    "fullname": ("👤", "ПО ФИО",     "Введи имя или ФИО:\n<i>Пример: <code>Иванов Иван</code></i>"),
    "face":     ("📸", "ПО ЛИЦУ",    None),
}

# ─── Клавиатуры ──────────────────────────────────────────────────────────────
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔍 Метаданные",      callback_data="menu:meta"),
            InlineKeyboardButton(text="🔎 Username Finder", callback_data="menu:username"),
        ],
        [
            InlineKeyboardButton(text="🕵️ OSINT Центр",     callback_data="menu:osint"),
            InlineKeyboardButton(text="📡 Telegram Info",   callback_data="menu:tginfo"),
        ],
        [
            InlineKeyboardButton(text="👤 Профиль",         callback_data="menu:profile"),
            InlineKeyboardButton(text="💎 Подписка",        callback_data="menu:sub"),
        ],
        [
            InlineKeyboardButton(text="❓ Помощь",          callback_data="menu:help"),
            InlineKeyboardButton(text="⚙️ Настройки",       callback_data="menu:settings"),
        ],
    ])

def home_btn() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🏠 На главную", callback_data="nav:home"),
    ]])

def back_home_kb(back_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ Назад", callback_data=back_cb),
        InlineKeyboardButton(text="🏠 Главная", callback_data="nav:home"),
    ]])

def cancel_kb(back_cb: str = "nav:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data=back_cb),
    ]])

def osint_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 По номеру",      callback_data="osint:phone"),
            InlineKeyboardButton(text="📧 По email",       callback_data="osint:email"),
        ],
        [
            InlineKeyboardButton(text="🌐 По домену 🚧",   callback_data="osint:domain"),
            InlineKeyboardButton(text="🌍 По IP 🚧",       callback_data="osint:ip"),
        ],
        [
            InlineKeyboardButton(text="🎭 По нику 🚧",     callback_data="osint:nick"),
            InlineKeyboardButton(text="👤 По ФИО 🚧",      callback_data="osint:fullname"),
        ],
        [InlineKeyboardButton(text="📸 По лицу",           callback_data="osint:face")],
        [InlineKeyboardButton(text="🏠 На главную",        callback_data="nav:home")],
    ])

def links_kb(category: str, query: str) -> InlineKeyboardMarkup:
    q = quote_plus(query)
    links = OSINT_LINKS.get(category, [])
    buttons = []
    for item in links:
        url = item["url"].replace("{q}", q).replace("{raw}", query)
        buttons.append([InlineKeyboardButton(text=f"🔗 {item['name']}", url=url)])
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="menu:osint"),
        InlineKeyboardButton(text="🏠 Главная", callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ─── Тексты главного меню ────────────────────────────────────────────────────
HOME_TEXT = (
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "  🔮 <b>VEXIS</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "Выбери нужный раздел 👇"
)

# ─── /start ──────────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    name = message.from_user.first_name or "пользователь"
    user_id = message.from_user.id

    # Удаляем старое окно если есть
    old = _window.get(user_id)
    if old:
        try:
            await message.bot.delete_message(old[0], old[1])
        except Exception:
            pass

    # Приветственное фото (отдельное, не удаляется)
    if _WELCOME_PHOTO.exists():
        await message.answer_photo(
            photo=FSInputFile(_WELCOME_PHOTO),
            caption=(
                f"👋 Привет, <b>{name}</b>!\n\n"
                f"Я — <b>Vexis</b>, твой помощник для анализа данных,\n"
                f"OSINT и защиты приватности."
            ),
        )

    # Окно-меню (единственное редактируемое сообщение)
    win = await message.answer(HOME_TEXT, reply_markup=main_menu_kb())
    _window[user_id] = (win.chat.id, win.message_id)

# ─── nav:home ────────────────────────────────────────────────────────────────
@router.callback_query(F.data == "nav:home")
async def cb_home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(HOME_TEXT, reply_markup=main_menu_kb())
    await callback.answer()

# ─── Главное меню — разделы ───────────────────────────────────────────────────
@router.callback_query(F.data == "menu:meta")
async def cb_meta(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MetaState.waiting)
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔍 <b>АНАЛИЗ МЕТАДАННЫХ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📎 Отправь файл как <b>документ</b> (не как фото!)\n\n"
        "Поддерживаются:\n"
        "<code>JPG, PNG, MP3, MP4, PDF, DOCX, XLSX, PPTX</code>",
        reply_markup=cancel_kb("nav:home"),
    )
    await callback.answer()

@router.callback_query(F.data == "menu:username")
async def cb_username(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(UsernameState.length)
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔎 <b>USERNAME FINDER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Шаг 1/3 — Длина никнейма:\n\n"
        "Введи диапазон: <code>4-8</code>\n"
        "или одно число: <code>6</code>",
        reply_markup=cancel_kb("nav:home"),
    )
    await callback.answer()

@router.callback_query(F.data == "menu:osint")
async def cb_osint(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🕵️ <b>OSINT ЦЕНТР</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выбери категорию поиска:",
        reply_markup=osint_menu_kb(),
    )
    await callback.answer()

@router.callback_query(F.data == "menu:tginfo")
async def cb_tginfo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TgInfoState.waiting)
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  📡 <b>TELEGRAM INFO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Введи @username или числовой Telegram ID:",
        reply_markup=cancel_kb("nav:home"),
    )
    await callback.answer()

@router.callback_query(F.data == "menu:profile")
async def cb_profile(callback: CallbackQuery) -> None:
    u = callback.from_user
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  👤 <b>ПРОФИЛЬ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"┣ <b>ID:</b> <code>{u.id}</code>\n"
        f"┣ <b>Имя:</b> {u.full_name}\n"
        f"┣ <b>Username:</b> {'@' + u.username if u.username else '—'}\n"
        f"┣ <b>Тариф:</b> Free 🆓\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=home_btn(),
    )
    await callback.answer()

@router.callback_query(F.data == "menu:sub")
async def cb_sub(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  💎 <b>ПОДПИСКА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "┣ 🆓 <b>Free</b> — бесплатно\n"
        "┃   5 запросов/день\n\n"
        "┣ ⭐ <b>Premium</b> — $3 / 30 дней\n"
        "┃   50 запросов/день + OSINT\n\n"
        "┗ 👑 <b>VIP</b> — $7 / 90 дней\n"
        "    200 запросов/день + OSINT\n\n"
        "<i>Оплата через @Fragment или Stars</i>",
        reply_markup=home_btn(),
    )
    await callback.answer()

@router.callback_query(F.data == "menu:help")
async def cb_help(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  ❓ <b>ПОМОЩЬ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔍 <b>Метаданные</b>\n"
        "   Отправь файл документом → получи все данные\n\n"
        "🔎 <b>Username Finder</b>\n"
        "   Генерирует свободные никнеймы по параметрам\n\n"
        "🕵️ <b>OSINT Центр</b>\n"
        "   Введи запрос → получи ссылки на проверку\n\n"
        "📡 <b>Telegram Info</b>\n"
        "   Введи @username или ID → узнай всё об аккаунте\n\n"
        "<i>⚠️ Всё в рамках публичных данных</i>",
        reply_markup=home_btn(),
    )
    await callback.answer()

@router.callback_query(F.data == "menu:settings")
async def cb_settings(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  ⚙️ <b>НАСТРОЙКИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🚧 Раздел в разработке.\n"
        "Скоро здесь появятся настройки языка,\n"
        "уведомлений и приватности.",
        reply_markup=home_btn(),
    )
    await callback.answer()

# ─── OSINT категории ─────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("osint:"))
async def cb_osint_category(callback: CallbackQuery, state: FSMContext) -> None:
    cat = callback.data.split(":", 1)[1]
    ui = OSINT_UI.get(cat)
    if not ui:
        await callback.answer()
        return
    emoji, title, hint = ui

    # По лицу — просто ссылки, без ввода
    if cat == "face":
        links = OSINT_LINKS["face"]
        buttons = [[InlineKeyboardButton(text=f"🔗 {l['name']}", url=l["url"])] for l in links]
        buttons.append([
            InlineKeyboardButton(text="◀️ Назад", callback_data="menu:osint"),
            InlineKeyboardButton(text="🏠 Главная", callback_data="nav:home"),
        ])
        await callback.message.edit_text(
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "  📸 <b>ПОИСК ПО ЛИЦУ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Загрузи фото прямо на один из сайтов 👇",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()
        return

    # Категории в разработке
    if not OSINT_LINKS.get(cat):
        await callback.message.edit_text(
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"  {emoji} <b>{title}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🚧 <b>Скоро добавим!</b>\n\n"
            f"Эта категория в разработке.\n"
            f"Следи за обновлениями 👀",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀️ Назад", callback_data="menu:osint"),
            ]]),
        )
        await callback.answer("Скоро!", show_alert=False)
        return

    # Обычные категории — ждём ввод
    await state.update_data(osint_cat=cat)
    await state.set_state(OsintState.waiting)
    await callback.message.edit_text(
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {emoji} <b>OSINT {title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{hint}\n\n"
        f"<i>✏️ Напишите запрос в чат:</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Отмена", callback_data="menu:osint"),
        ]]),
    )
    await callback.answer()

@router.message(OsintState.waiting)
async def osint_query(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cat = data.get("osint_cat", "")
    query = (message.text or "").strip()
    await delete_user_msg(message)
    await state.clear()

    ui = OSINT_UI.get(cat, ("🔍", "ПРОВЕРКА", ""))
    emoji, title, _ = ui
    cnt = len(OSINT_LINKS.get(cat, []))

    await edit_window(
        message.bot,
        message.from_user.id,
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {emoji} <b>OSINT {title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔍 Запрос: <code>{query}</code>\n"
        f"📋 Источников: <b>{cnt}</b>\n\n"
        f"Нажми для перехода на сайт 👇",
        links_kb(cat, query),
    )

# ─── Метаданные — ввод через документ ────────────────────────────────────────
@router.message(MetaState.waiting, F.document)
async def handle_document(message: Message, state: FSMContext) -> None:
    doc = message.document
    fname = doc.file_name or "unknown"
    ext = Path(fname).suffix.lower()
    user_id = message.from_user.id

    await delete_user_msg(message)
    await edit_window(message.bot, user_id,
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔍 <b>МЕТАДАННЫЕ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⏳ Анализирую файл..."
    )

    meta: dict[str, str] = {}
    try:
        file = await message.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await message.bot.download_file(file.file_path, destination=buf)
        buf.seek(0)
        data = buf.read()

        if ext in (".jpg", ".jpeg", ".png", ".tiff", ".webp"):
            try:
                from PIL import Image
                from PIL.ExifTags import TAGS
                img = Image.open(io.BytesIO(data))
                exif_data = getattr(img, "_getexif", lambda: None)() or {}
                for tag_id, val in exif_data.items():
                    tag = TAGS.get(tag_id, str(tag_id))
                    if not isinstance(val, bytes):
                        meta[tag] = str(val)
                if not meta:
                    meta["⚠️ EXIF"] = "Нет данных (файл сжат или без EXIF)"
                meta["Формат"] = img.format or ext.upper()
                meta["Размер"] = f"{img.width}×{img.height} px"
                meta["Режим"] = img.mode
            except Exception as e:
                meta["Ошибка"] = str(e)

        elif ext in (".mp3", ".flac", ".ogg", ".m4a", ".wav"):
            try:
                from mutagen import File as MutagenFile
                audio = MutagenFile(io.BytesIO(data))
                if audio and audio.tags:
                    for k, v in audio.tags.items():
                        meta[str(k)] = str(v)
                if hasattr(audio, "info"):
                    meta["Длина"] = f"{audio.info.length:.1f} сек"
                    meta["Битрейт"] = f"{getattr(audio.info, 'bitrate', '?')} kbps"
                if not meta:
                    meta["⚠️ Теги"] = "Не найдены"
            except Exception as e:
                meta["Ошибка"] = str(e)

        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(data))
                info = reader.metadata or {}
                for k, v in info.items():
                    meta[k.lstrip("/")] = str(v)
                meta["Страниц"] = str(len(reader.pages))
                if not meta:
                    meta["⚠️ Метаданные"] = "Не найдены"
            except Exception as e:
                meta["Ошибка"] = str(e)

        elif ext == ".docx":
            try:
                from docx import Document
                doc_obj = Document(io.BytesIO(data))
                cp = doc_obj.core_properties
                for attr in ("author", "title", "subject", "keywords", "created", "modified", "last_modified_by"):
                    val = getattr(cp, attr, None)
                    if val:
                        meta[attr] = str(val)
                if not meta:
                    meta["⚠️ Свойства"] = "Не найдены"
            except Exception as e:
                meta["Ошибка"] = str(e)

        elif ext in (".xlsx", ".xls"):
            try:
                from openpyxl import load_workbook
                wb = load_workbook(io.BytesIO(data))
                cp = wb.properties
                for attr in ("creator", "title", "subject", "keywords", "created", "modified", "lastModifiedBy"):
                    val = getattr(cp, attr, None)
                    if val:
                        meta[attr] = str(val)
                if not meta:
                    meta["⚠️ Свойства"] = "Не найдены"
            except Exception as e:
                meta["Ошибка"] = str(e)

        elif ext == ".pptx":
            try:
                from pptx import Presentation
                prs = Presentation(io.BytesIO(data))
                cp = prs.core_properties
                for attr in ("author", "title", "subject", "keywords", "created", "modified"):
                    val = getattr(cp, attr, None)
                    if val:
                        meta[attr] = str(val)
                if not meta:
                    meta["⚠️ Свойства"] = "Не найдены"
            except Exception as e:
                meta["Ошибка"] = str(e)

        else:
            meta["⚠️ Формат"] = f"Неподдерживаемый: {ext}"

    except Exception as e:
        logger.exception("Metadata error")
        meta["❌ Ошибка"] = str(e)[:150]

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "  🔍 <b>МЕТАДАННЫЕ</b>",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
        f"📄 <code>{fname}</code>",
        f"📦 {doc.file_size:,} байт\n",
    ]
    for k, v in list(meta.items())[:25]:
        lines.append(f"┣ <b>{k}:</b> <code>{str(v)[:80]}</code>")
    lines.append("┗━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"\n📋 Полей: <b>{len(meta)}</b>")

    await edit_window(
        message.bot, user_id,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📎 Отправить ещё файл", callback_data="menu:meta"),
            InlineKeyboardButton(text="🏠 Главная", callback_data="nav:home"),
        ]]),
    )
    # Остаёмся в состоянии — ждём следующий файл (или нажатие кнопки)

# ─── Username Finder ─────────────────────────────────────────────────────────
@router.message(UsernameState.length)
async def username_step_length(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await delete_user_msg(message)
    m = re.match(r"^(\d+)(?:-(\d+))?$", text)
    if not m:
        await edit_window(message.bot, message.from_user.id,
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "  🔎 <b>USERNAME FINDER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ Неверный формат!\n\n"
            "Шаг 1/3 — Длина никнейма:\n"
            "Введи диапазон: <code>4-8</code>\n"
            "или одно число: <code>6</code>",
            cancel_kb("nav:home"),
        )
        return
    mn = int(m.group(1))
    mx = int(m.group(2) or m.group(1))
    await state.update_data(len_min=mn, len_max=mx)
    await state.set_state(UsernameState.count)
    await edit_window(message.bot, message.from_user.id,
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔎 <b>USERNAME FINDER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Длина: <b>{mn}–{mx}</b>\n\n"
        "Шаг 2/3 — Сколько вариантов?\n"
        "Введи число от <code>1</code> до <code>50</code>:",
        cancel_kb("nav:home"),
    )

@router.message(UsernameState.count)
async def username_step_count(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await delete_user_msg(message)
    if not text.isdigit() or not 1 <= int(text) <= 50:
        await edit_window(message.bot, message.from_user.id,
            "⚠️ Введи число от 1 до 50:", cancel_kb("nav:home"))
        return
    await state.update_data(count=int(text))
    await state.set_state(UsernameState.type)
    await edit_window(message.bot, message.from_user.id,
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  🔎 <b>USERNAME FINDER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Количество: <b>{text}</b>\n\n"
        "Шаг 3/3 — Тип никнейма:\n\n"
        "<code>1</code> — только буквы\n"
        "<code>2</code> — буквы + цифры\n"
        "<code>3</code> — буквы + цифры + _\n\n"
        "Введи 1, 2 или 3:",
        cancel_kb("nav:home"),
    )

@router.message(UsernameState.type)
async def username_step_type(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await delete_user_msg(message)
    if text not in ("1", "2", "3"):
        await edit_window(message.bot, message.from_user.id,
            "⚠️ Введи 1, 2 или 3:", cancel_kb("nav:home"))
        return

    data = await state.get_data()
    await state.clear()

    charsets = {
        "1": string.ascii_lowercase,
        "2": string.ascii_lowercase + string.digits,
        "3": string.ascii_lowercase + string.digits + "_",
    }
    charset = charsets[text]
    mn, mx, cnt = data["len_min"], data["len_max"], data["count"]

    usernames: list[str] = []
    for _ in range(cnt * 10):
        if len(usernames) >= cnt:
            break
        length = random.randint(mn, mx)
        name = "".join(random.choices(charset, k=length))
        if name.startswith("_") or name.endswith("_"):
            continue
        usernames.append(name)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "  🔎 <b>USERNAME FINDER</b>",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
        f"📊 Длина: {mn}–{mx} | Тип: {text} | Кол-во: {cnt}\n",
    ]
    for u in usernames:
        lines.append(f"  ┣ <code>@{u}</code>")
    lines.append("  ┗━━━━━━━━━━━━━━━━━━━━")
    lines.append("\n<i>⚠️ Доступность проверяй через @Fragment</i>")

    await edit_window(
        message.bot, message.from_user.id,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Ещё раз", callback_data="menu:username"),
            InlineKeyboardButton(text="🏠 Главная", callback_data="nav:home"),
        ]]),
    )

# ─── Telegram Info ───────────────────────────────────────────────────────────
@router.message(TgInfoState.waiting)
async def tginfo_query(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    user_id = message.from_user.id
    await delete_user_msg(message)
    await state.clear()

    await edit_window(message.bot, user_id,
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "  📡 <b>TELEGRAM INFO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⏳ Ищу..."
    )

    try:
        if query.startswith("@"):
            chat = await message.bot.get_chat(query)
        elif query.lstrip("-").isdigit():
            chat = await message.bot.get_chat(int(query))
        else:
            await edit_window(message.bot, user_id,
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "  📡 <b>TELEGRAM INFO</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ Введи @username или числовой ID\n\n"
                "<i>✏️ Попробуй ещё раз:</i>",
                cancel_kb("nav:home"),
            )
            await state.set_state(TgInfoState.waiting)
            return

        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━",
            "  📡 <b>TELEGRAM INFO</b>",
            "━━━━━━━━━━━━━━━━━━━━━━\n",
            f"┣ <b>ID:</b> <code>{chat.id}</code>",
            f"┣ <b>Тип:</b> {chat.type}",
            f"┣ <b>Имя:</b> {chat.full_name or chat.title or '—'}",
            f"┣ <b>Username:</b> {'@' + chat.username if chat.username else '—'}",
            f"┣ <b>Описание:</b> {(chat.bio or getattr(chat, 'description', None) or '—')[:80]}",
            "┗━━━━━━━━━━━━━━━━━━━━━",
        ]
        await edit_window(
            message.bot, user_id,
            "\n".join(lines),
            InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔍 Ещё поиск", callback_data="menu:tginfo"),
                InlineKeyboardButton(text="🏠 Главная", callback_data="nav:home"),
            ]]),
        )
    except Exception as e:
        await edit_window(
            message.bot, user_id,
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "  📡 <b>TELEGRAM INFO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"❌ Не найдено: <code>{str(e)[:100]}</code>\n\n"
            "<i>✏️ Попробуй ещё раз:</i>",
            cancel_kb("nav:home"),
        )
        await state.set_state(TgInfoState.waiting)

# ─── Запуск ──────────────────────────────────────────────────────────────────
_dp: Dispatcher | None = None

def _get_dp() -> Dispatcher:
    global _dp
    if _dp is None:
        _dp = Dispatcher(storage=MemoryStorage())
        _dp.include_router(router)
    return _dp

async def _start_bot(proxy_url: str | None) -> None:
    session = _make_session(proxy_url)
    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = _get_dp()
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("🔮 Vexis запущен%s", f" через {proxy_url}" if proxy_url else " без прокси")
    await dp.start_polling(bot)


async def main() -> None:
    if _PROXIES:
        logger.info("🔒 Прокси загружено: %d шт.", len(_PROXIES))
        for i, proxy_url in enumerate(_PROXIES, 1):
            logger.info("🔄 Пробую прокси [%d/%d]: %s", i, len(_PROXIES), proxy_url)
            try:
                await _start_bot(proxy_url)
                return
            except Exception as e:
                logger.warning("❌ %s → %s", proxy_url, e)
        logger.warning("⚠️ Все прокси недоступны, запускаю без прокси")

    await _start_bot(None)


if __name__ == "__main__":
    asyncio.run(main())
