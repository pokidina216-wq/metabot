"""
╔══════════════════════════════════════╗
║        V E X I S   L I T E           ║
║   Intelligence & Privacy Platform    ║
╚══════════════════════════════════════╝

Архитектура «одного окна»:
  /start  →  приветственное фото + единое окно-меню
  Все секции редактируют одно сообщение
  Сообщения пользователя автоудаляются
  Документы анализируются в окне

Env-переменные:
  BOT_TOKEN   — токен от @BotFather
  ADMIN_IDS   — ID через запятую (811971538,...)
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import string
import random
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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════
#  КОНФИГ
# ═══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vexis")

BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
ADMIN_IDS  = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
VERSION    = "1.0.0"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан! Укажи переменную окружения.")

_WELCOME_PHOTO = Path(__file__).parent / "welcome.png"

# ═══════════════════════════════════════════════════════════
#  ПРОКСИ
# ═══════════════════════════════════════════════════════════
def _load_proxies() -> list[str]:
    p = Path(__file__).parent / "proxies.txt"
    if not p.exists():
        return []
    proxies = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("socks", "http")):
            proxies.append(line)
        else:
            port = int(line.split(":")[-1])
            proto = "http" if port in (80, 8080, 3128, 8118, 8888, 3000) else "socks5"
            proxies.append(f"{proto}://{line}")
    return proxies

_PROXIES = _load_proxies()

def _make_session(proxy_url: str | None = None) -> AiohttpSession:
    return AiohttpSession(proxy=proxy_url) if proxy_url else AiohttpSession()

# ═══════════════════════════════════════════════════════════
#  ОКНО-МЕНЕДЖЕР
# ═══════════════════════════════════════════════════════════
# user_id → (chat_id, message_id)
_windows: dict[int, tuple[int, int]] = {}

async def set_window(bot: Bot, user_id: int, text: str,
                     kb: InlineKeyboardMarkup | None = None) -> None:
    """Редактирует текущее окно пользователя."""
    entry = _windows.get(user_id)
    if not entry:
        return
    chat_id, msg_id = entry
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=text, parse_mode="HTML", reply_markup=kb,
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning("set_window: %s", e)

async def _del(msg: Message) -> None:
    """Тихо удаляет сообщение."""
    try:
        await msg.delete()
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════
#  РОУТЕР И FSM
# ═══════════════════════════════════════════════════════════
router = Router()

class OsintState(StatesGroup):
    waiting = State()

class UsernameState(StatesGroup):
    length = State()
    count  = State()
    style  = State()

class TgInfoState(StatesGroup):
    waiting = State()

class MetaState(StatesGroup):
    waiting = State()

# ═══════════════════════════════════════════════════════════
#  ДИЗАЙН-СИСТЕМА
# ═══════════════════════════════════════════════════════════
# Все экраны используют единый заголовок-блок
def header(icon: str, title: str) -> str:
    return (
        f"╔══════════════════════════╗\n"
        f"║  {icon}  <b>{title}</b>\n"
        f"╚══════════════════════════╝"
    )

def divider() -> str:
    return "──────────────────────────────"

HOME_TEXT = (
    "╔══════════════════════════╗\n"
    "║  🔮  <b>V E X I S</b>\n"
    "║  <i>Intelligence Platform v1.0</i>\n"
    "╚══════════════════════════╝\n\n"
    "Твой инструмент для анализа данных,\n"
    "OSINT-разведки и защиты приватности.\n\n"
    "──────────────────────────────\n"
    "Выбери раздел 👇"
)

# ═══════════════════════════════════════════════════════════
#  OSINT ДАННЫЕ
# ═══════════════════════════════════════════════════════════
OSINT_META: dict[str, tuple[str, str, str]] = {
    # cat: (emoji, заголовок, подсказка)
    "phone":    ("📱", "ПО НОМЕРУ",  "Введи номер в формате <code>+79001234567</code>"),
    "email":    ("📧", "ПО EMAIL",   "Введи адрес: <code>user@example.com</code>"),
    "domain":   ("🌐", "ПО ДОМЕНУ",  "Введи домен: <code>example.com</code>"),
    "ip":       ("🌍", "ПО IP",      "Введи IP: <code>8.8.8.8</code>"),
    "nick":     ("🎭", "ПО НИКУ",    "Введи никнейм: <code>cooluser42</code>"),
    "fullname": ("👤", "ПО ФИО",     "Введи имя: <code>Иванов Иван Иванович</code>"),
    "face":     ("📸", "ПО ЛИЦУ",    None),
}

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
    "face": [
        {"name": "Search4Faces",    "url": "https://search4faces.com/"},
        {"name": "Яндекс.Картинки", "url": "https://yandex.ru/images/search"},
        {"name": "BetaFace",        "url": "https://betaface.com/demo_old.html"},
        {"name": "OK — Люди",       "url": "https://ok.ru/search?st.mode=Users&st.vpl.mini=fa"},
        {"name": "Online-VK",       "url": "https://online-vk.ru/"},
    ],
    "domain":   [],
    "ip":       [],
    "nick":     [],
    "fullname": [],
}

# ═══════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════
def _btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)

def _url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)

def kb_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("🏠 Главное меню", "nav:home")]])

def kb_back_home(back_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        _btn("◀️ Назад", back_cb),
        _btn("🏠 Меню", "nav:home"),
    ]])

def kb_cancel(back_cb: str = "nav:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("✖ Отмена", back_cb)]])

def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn("🔍 Метаданные",      "menu:meta"),
            _btn("🔎 Username Finder", "menu:username"),
        ],
        [
            _btn("🕵️ OSINT Центр",     "menu:osint"),
            _btn("📡 Telegram Info",   "menu:tginfo"),
        ],
        [
            _btn("👤 Профиль",         "menu:profile"),
            _btn("💎 Тарифы",          "menu:plans"),
        ],
        [
            _btn("❓ Помощь",          "menu:help"),
            _btn("⚙️ Настройки",       "menu:settings"),
        ],
    ])

def kb_osint_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            _btn("📱 По номеру",    "osint:phone"),
            _btn("📧 По email",     "osint:email"),
        ],
        [
            _btn("🌐 По домену 🚧", "osint:domain"),
            _btn("🌍 По IP 🚧",     "osint:ip"),
        ],
        [
            _btn("🎭 По нику 🚧",   "osint:nick"),
            _btn("👤 По ФИО 🚧",    "osint:fullname"),
        ],
        [_btn("📸 По лицу",         "osint:face")],
        [_btn("◀️ Назад в меню",    "nav:home")],
    ])

def kb_osint_links(category: str, query: str) -> InlineKeyboardMarkup:
    q = quote_plus(query)
    links = OSINT_LINKS.get(category, [])
    rows = []
    for item in links:
        url = item["url"].replace("{q}", q).replace("{raw}", query)
        rows.append([_url_btn(f"🔗 {item['name']}", url)])
    rows.append([
        _btn("◀️ OSINT", "menu:osint"),
        _btn("🏠 Меню",  "nav:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ═══════════════════════════════════════════════════════════
#  КОМАНДА /start
# ═══════════════════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    name    = message.from_user.first_name or "пользователь"

    # Удаляем старое окно
    old = _windows.get(user_id)
    if old:
        try:
            await message.bot.delete_message(old[0], old[1])
        except Exception:
            pass

    # Приветственное фото
    if _WELCOME_PHOTO.exists():
        caption = (
            f"👋 Привет, <b>{name}</b>!\n\n"
            f"Добро пожаловать в <b>Vexis</b> — платформу\n"
            f"для OSINT, анализа данных и разведки.\n\n"
            f"<i>Все функции доступны прямо сейчас.</i>"
        )
        await message.answer_photo(
            photo=FSInputFile(_WELCOME_PHOTO),
            caption=caption,
        )

    # Создаём окно-меню
    win = await message.answer(HOME_TEXT, reply_markup=kb_main_menu())
    _windows[user_id] = (win.chat.id, win.message_id)

# ═══════════════════════════════════════════════════════════
#  НАВИГАЦИЯ
# ═══════════════════════════════════════════════════════════
@router.callback_query(F.data == "nav:home")
async def nav_home(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text(HOME_TEXT, reply_markup=kb_main_menu())
    await cb.answer()

# ═══════════════════════════════════════════════════════════
#  РАЗДЕЛЫ ГЛАВНОГО МЕНЮ
# ═══════════════════════════════════════════════════════════

# ── Метаданные ───────────────────────────────────────────
@router.callback_query(F.data == "menu:meta")
async def cb_meta(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MetaState.waiting)
    await cb.message.edit_text(
        header("🔍", "МЕТАДАННЫЕ") + "\n\n"
        "Отправь файл как <b>документ</b> (не как фото).\n\n"
        f"{divider()}\n"
        "Форматы:\n"
        "  🖼  <code>JPG PNG TIFF WEBP</code>\n"
        "  🎵  <code>MP3 FLAC OGG M4A WAV</code>\n"
        "  📄  <code>PDF DOCX XLSX PPTX</code>\n\n"
        "<i>⚠️ Изображения должны быть отправлены именно\n"
        "как «Файл», иначе Telegram уберёт EXIF.</i>",
        reply_markup=kb_cancel("nav:home"),
    )
    await cb.answer()

# ── Username Finder ──────────────────────────────────────
@router.callback_query(F.data == "menu:username")
async def cb_username(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(UsernameState.length)
    await cb.message.edit_text(
        header("🔎", "USERNAME FINDER") + "\n\n"
        "Генератор свободных никнеймов.\n\n"
        f"{divider()}\n"
        "<b>Шаг 1 из 3</b> — Длина\n\n"
        "Введи диапазон: <code>4-8</code>\n"
        "или точное число: <code>6</code>",
        reply_markup=kb_cancel("nav:home"),
    )
    await cb.answer()

# ── OSINT ─────────────────────────────────────────────────
@router.callback_query(F.data == "menu:osint")
async def cb_osint(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text(
        header("🕵️", "OSINT ЦЕНТР") + "\n\n"
        "Открытая разведка по цифровым следам.\n\n"
        f"{divider()}\n"
        "Выбери категорию поиска 👇",
        reply_markup=kb_osint_menu(),
    )
    await cb.answer()

# ── Telegram Info ─────────────────────────────────────────
@router.callback_query(F.data == "menu:tginfo")
async def cb_tginfo(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TgInfoState.waiting)
    await cb.message.edit_text(
        header("📡", "TELEGRAM INFO") + "\n\n"
        "Получи публичные данные о любом аккаунте\n"
        "или канале в Telegram.\n\n"
        f"{divider()}\n"
        "Введи <b>@username</b> или числовой <b>ID</b>:",
        reply_markup=kb_cancel("nav:home"),
    )
    await cb.answer()

# ── Профиль ───────────────────────────────────────────────
@router.callback_query(F.data == "menu:profile")
async def cb_profile(cb: CallbackQuery) -> None:
    u = cb.from_user
    uname = f"@{u.username}" if u.username else "не задан"
    await cb.message.edit_text(
        header("👤", "МОЙ ПРОФИЛЬ") + "\n\n"
        f"  <b>Имя:</b>      {u.full_name}\n"
        f"  <b>Username:</b> {uname}\n"
        f"  <b>ID:</b>       <code>{u.id}</code>\n\n"
        f"{divider()}\n"
        f"  <b>Тариф:</b>    🆓 Free\n"
        f"  <b>Запросов:</b> ∞ (нет лимита)\n"
        f"  <b>Статус:</b>   ✅ Активен",
        reply_markup=kb_home(),
    )
    await cb.answer()

# ── Тарифы ────────────────────────────────────────────────
@router.callback_query(F.data == "menu:plans")
async def cb_plans(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        header("💎", "ТАРИФЫ И ПОДПИСКА") + "\n\n"
        "🆓  <b>Free</b>        — <b>0 ₽</b>\n"
        "     Базовый доступ, метаданные,\n"
        "     username finder, TG Info\n\n"
        "⭐  <b>Premium</b>     — <b>$3 / мес</b>\n"
        "     150 Stars · Полный OSINT,\n"
        "     50 запросов/день\n\n"
        "👑  <b>VIP</b>         — <b>$7 / 90 дней</b>\n"
        "     350 Stars · Всё включено,\n"
        "     200 запросов/день\n\n"
        f"{divider()}\n"
        "<i>Оплата через Telegram Stars / @Fragment</i>",
        reply_markup=kb_home(),
    )
    await cb.answer()

# ── Помощь ────────────────────────────────────────────────
@router.callback_query(F.data == "menu:help")
async def cb_help(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        header("❓", "СПРАВКА") + "\n\n"
        "🔍  <b>Метаданные</b>\n"
        "    Отправь файл как документ → получи\n"
        "    все скрытые данные (EXIF, автор и т.д.)\n\n"
        "🔎  <b>Username Finder</b>\n"
        "    Задай параметры → получи список\n"
        "    сгенерированных никнеймов\n\n"
        "🕵️  <b>OSINT Центр</b>\n"
        "    Введи запрос → прямые ссылки\n"
        "    на проверку в открытых источниках\n\n"
        "📡  <b>Telegram Info</b>\n"
        "    Введи @username или ID → публичная\n"
        "    информация об аккаунте / канале\n\n"
        f"{divider()}\n"
        "<i>Весь функционал работает в рамках\n"
        "публично доступных данных.</i>",
        reply_markup=kb_home(),
    )
    await cb.answer()

# ── Настройки ─────────────────────────────────────────────
@router.callback_query(F.data == "menu:settings")
async def cb_settings(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        header("⚙️", "НАСТРОЙКИ") + "\n\n"
        "🚧  Раздел в разработке.\n\n"
        f"{divider()}\n"
        "Скоро здесь появятся:\n"
        "  • Язык интерфейса\n"
        "  • Уведомления\n"
        "  • Настройки приватности\n"
        "  • Управление данными",
        reply_markup=kb_home(),
    )
    await cb.answer()

# ═══════════════════════════════════════════════════════════
#  OSINT — КАТЕГОРИИ
# ═══════════════════════════════════════════════════════════
@router.callback_query(F.data.startswith("osint:"))
async def cb_osint_category(cb: CallbackQuery, state: FSMContext) -> None:
    cat = cb.data.split(":", 1)[1]
    meta = OSINT_META.get(cat)
    if not meta:
        await cb.answer()
        return
    emoji, title, hint = meta

    # По лицу — просто список ссылок
    if cat == "face":
        rows = [[_url_btn(f"🔗 {l['name']}", l["url"])] for l in OSINT_LINKS["face"]]
        rows.append([_btn("◀️ Назад", "menu:osint"), _btn("🏠 Меню", "nav:home")])
        await cb.message.edit_text(
            header(emoji, f"OSINT  —  {title}") + "\n\n"
            "Загрузи фото на один из сайтов ниже.\n"
            "Сервисы ищут совпадения по базам.\n\n"
            f"{divider()}\n"
            "Выбери сервис 👇",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await cb.answer()
        return

    # Категория в разработке
    if not OSINT_LINKS.get(cat):
        await cb.message.edit_text(
            header(emoji, f"OSINT  —  {title}") + "\n\n"
            "🚧  <b>Скоро!</b>\n\n"
            f"{divider()}\n"
            "Эта категория сейчас в разработке.\n"
            "Следи за обновлениями.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                _btn("◀️ Назад", "menu:osint"),
            ]]),
        )
        await cb.answer("Скоро доступно!", show_alert=False)
        return

    # Обычные категории — ожидаем ввод
    await state.update_data(osint_cat=cat)
    await state.set_state(OsintState.waiting)
    await cb.message.edit_text(
        header(emoji, f"OSINT  —  {title}") + "\n\n"
        f"{divider()}\n"
        f"{hint}\n\n"
        "<i>✏️ Напиши запрос в чат:</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            _btn("✖ Отмена", "menu:osint"),
        ]]),
    )
    await cb.answer()

@router.message(OsintState.waiting)
async def osint_input(message: Message, state: FSMContext) -> None:
    data  = await state.get_data()
    cat   = data.get("osint_cat", "")
    query = (message.text or "").strip()
    await _del(message)
    await state.clear()

    meta  = OSINT_META.get(cat, ("🔍", "ПОИСК", ""))
    emoji, title, _ = meta
    count = len(OSINT_LINKS.get(cat, []))

    await set_window(
        message.bot, message.from_user.id,
        header(emoji, f"OSINT  —  {title}") + "\n\n"
        f"  <b>Запрос:</b>    <code>{query}</code>\n"
        f"  <b>Источников:</b> {count}\n\n"
        f"{divider()}\n"
        "Нажми на сервис для перехода 👇",
        kb_osint_links(cat, query),
    )

# ═══════════════════════════════════════════════════════════
#  МЕТАДАННЫЕ — ОБРАБОТКА ДОКУМЕНТА
# ═══════════════════════════════════════════════════════════
@router.message(MetaState.waiting, F.document)
async def handle_document(message: Message, state: FSMContext) -> None:
    doc     = message.document
    fname   = doc.file_name or "unknown"
    ext     = Path(fname).suffix.lower()
    user_id = message.from_user.id

    await _del(message)
    await set_window(message.bot, user_id,
        header("🔍", "МЕТАДАННЫЕ") + "\n\n"
        f"  <b>Файл:</b> <code>{fname}</code>\n\n"
        f"{divider()}\n"
        "⏳ Анализирую…",
    )

    meta: dict[str, str] = {}
    error: str | None = None

    try:
        file = await message.bot.get_file(doc.file_id)
        buf  = io.BytesIO()
        await message.bot.download_file(file.file_path, destination=buf)
        buf.seek(0)
        raw = buf.read()

        if ext in (".jpg", ".jpeg", ".png", ".tiff", ".webp"):
            try:
                from PIL import Image
                from PIL.ExifTags import TAGS
                img  = Image.open(io.BytesIO(raw))
                exif = getattr(img, "_getexif", lambda: None)() or {}
                for tag_id, val in exif.items():
                    tag = TAGS.get(tag_id, str(tag_id))
                    if not isinstance(val, (bytes, bytearray)):
                        meta[tag] = str(val)[:120]
                meta.setdefault("⚠️ EXIF", "Не найден (фото без метаданных)" if not meta else "OK")
                meta["Формат"] = img.format or ext.lstrip(".").upper()
                meta["Размер"] = f"{img.width} × {img.height} px"
                meta["Режим"]  = img.mode
            except Exception as e:
                error = str(e)

        elif ext in (".mp3", ".flac", ".ogg", ".m4a", ".wav"):
            try:
                from mutagen import File as MutagenFile
                audio = MutagenFile(io.BytesIO(raw))
                if audio and audio.tags:
                    for k, v in audio.tags.items():
                        meta[str(k)] = str(v)[:120]
                if hasattr(audio, "info"):
                    meta["Длина"]   = f"{audio.info.length:.1f} сек"
                    meta["Битрейт"] = f"{getattr(audio.info, 'bitrate', '?')} kbps"
                if not meta:
                    meta["⚠️ Теги"] = "Теги не обнаружены"
            except Exception as e:
                error = str(e)

        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(raw))
                info   = reader.metadata or {}
                for k, v in info.items():
                    meta[k.lstrip("/")] = str(v)[:120]
                meta["Страниц"] = str(len(reader.pages))
                if not meta:
                    meta["⚠️ Данные"] = "Метаданные отсутствуют"
            except Exception as e:
                error = str(e)

        elif ext == ".docx":
            try:
                from docx import Document
                cp = Document(io.BytesIO(raw)).core_properties
                for attr in ("author", "title", "subject", "keywords",
                             "created", "modified", "last_modified_by"):
                    val = getattr(cp, attr, None)
                    if val:
                        meta[attr] = str(val)[:120]
                if not meta:
                    meta["⚠️ Свойства"] = "Свойства документа не заданы"
            except Exception as e:
                error = str(e)

        elif ext in (".xlsx", ".xls"):
            try:
                from openpyxl import load_workbook
                cp = load_workbook(io.BytesIO(raw)).properties
                for attr in ("creator", "title", "subject", "keywords",
                             "created", "modified", "lastModifiedBy"):
                    val = getattr(cp, attr, None)
                    if val:
                        meta[attr] = str(val)[:120]
                if not meta:
                    meta["⚠️ Свойства"] = "Свойства файла не заданы"
            except Exception as e:
                error = str(e)

        elif ext == ".pptx":
            try:
                from pptx import Presentation
                cp = Presentation(io.BytesIO(raw)).core_properties
                for attr in ("author", "title", "subject", "keywords",
                             "created", "modified"):
                    val = getattr(cp, attr, None)
                    if val:
                        meta[attr] = str(val)[:120]
                if not meta:
                    meta["⚠️ Свойства"] = "Свойства презентации не заданы"
            except Exception as e:
                error = str(e)

        else:
            meta["⚠️ Формат"] = f"Не поддерживается: {ext}"

    except Exception as e:
        logger.exception("handle_document")
        error = str(e)[:200]

    # Строим результат
    size_kb = round(doc.file_size / 1024, 1)
    lines = [
        header("🔍", "МЕТАДАННЫЕ"),
        "",
        f"  📄  <code>{fname}</code>",
        f"  📦  {size_kb} КБ  ·  {ext.lstrip('.').upper() or '—'}",
        "",
        divider(),
    ]

    if error:
        lines += [f"", f"❌  <b>Ошибка анализа:</b>", f"<code>{error}</code>"]
    elif meta:
        lines.append(f"  <b>Найдено полей: {len(meta)}</b>")
        lines.append("")
        for i, (k, v) in enumerate(list(meta.items())[:25]):
            prefix = "┗" if i == len(meta) - 1 or i == 24 else "┣"
            lines.append(f"{prefix} <b>{k}:</b>  <code>{v}</code>")
    else:
        lines += ["", "ℹ️  Метаданные не найдены."]

    await set_window(
        message.bot, user_id,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=[[
            _btn("📎 Ещё файл",    "menu:meta"),
            _btn("🏠 Меню",        "nav:home"),
        ]]),
    )

# ═══════════════════════════════════════════════════════════
#  USERNAME FINDER — 3 ШАГА
# ═══════════════════════════════════════════════════════════
@router.message(UsernameState.length)
async def uf_step_length(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await _del(message)

    m = re.match(r"^(\d+)(?:[—\-–](\d+))?$", text)
    if not m:
        await set_window(message.bot, message.from_user.id,
            header("🔎", "USERNAME FINDER") + "\n\n"
            "⚠️  Неверный формат.\n\n"
            f"{divider()}\n"
            "<b>Шаг 1 из 3</b> — Длина\n\n"
            "Диапазон: <code>4-8</code>\n"
            "Точное число: <code>6</code>",
            kb_cancel("nav:home"),
        )
        return

    mn = int(m.group(1))
    mx = int(m.group(2) or m.group(1))
    mn, mx = min(mn, mx), max(mn, mx)
    mn = max(3, min(mn, 32))
    mx = max(3, min(mx, 32))

    await state.update_data(len_min=mn, len_max=mx)
    await state.set_state(UsernameState.count)
    await set_window(message.bot, message.from_user.id,
        header("🔎", "USERNAME FINDER") + "\n\n"
        f"  ✅  Длина: <b>{mn}–{mx}</b> символов\n\n"
        f"{divider()}\n"
        "<b>Шаг 2 из 3</b> — Количество\n\n"
        "Сколько вариантов? (от <code>1</code> до <code>50</code>):",
        kb_cancel("nav:home"),
    )

@router.message(UsernameState.count)
async def uf_step_count(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await _del(message)

    if not text.isdigit() or not 1 <= int(text) <= 50:
        await set_window(message.bot, message.from_user.id,
            "⚠️  Введи число от 1 до 50:",
            kb_cancel("nav:home"),
        )
        return

    await state.update_data(count=int(text))
    await state.set_state(UsernameState.style)
    await set_window(message.bot, message.from_user.id,
        header("🔎", "USERNAME FINDER") + "\n\n"
        f"  ✅  Количество: <b>{text}</b>\n\n"
        f"{divider()}\n"
        "<b>Шаг 3 из 3</b> — Стиль\n\n"
        "  <code>1</code>  — только буквы  <i>( abcde )</i>\n"
        "  <code>2</code>  — буквы + цифры  <i>( abc42 )</i>\n"
        "  <code>3</code>  — буквы + цифры + _  <i>( abc_42 )</i>\n\n"
        "Введи 1, 2 или 3:",
        kb_cancel("nav:home"),
    )

@router.message(UsernameState.style)
async def uf_step_style(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await _del(message)

    if text not in ("1", "2", "3"):
        await set_window(message.bot, message.from_user.id,
            "⚠️  Введи 1, 2 или 3:", kb_cancel("nav:home"))
        return

    data    = await state.get_data()
    await state.clear()

    charsets = {
        "1": string.ascii_lowercase,
        "2": string.ascii_lowercase + string.digits,
        "3": string.ascii_lowercase + string.digits + "_",
    }
    charset    = charsets[text]
    mn, mx, cnt = data["len_min"], data["len_max"], data["count"]
    style_name = {"1": "буквы", "2": "буквы + цифры", "3": "буквы + цифры + _"}[text]

    results: list[str] = []
    for _ in range(cnt * 20):
        if len(results) >= cnt:
            break
        length = random.randint(mn, mx)
        name   = "".join(random.choices(charset, k=length))
        if name.startswith("_") or name.endswith("_") or "__" in name:
            continue
        results.append(name)

    lines = [
        header("🔎", "USERNAME FINDER"),
        "",
        f"  <b>Длина:</b>      {mn}–{mx}",
        f"  <b>Стиль:</b>      {style_name}",
        f"  <b>Вариантов:</b>  {len(results)}",
        "",
        divider(),
        "",
    ]
    for name in results:
        lines.append(f"  ➤  <code>@{name}</code>")
    lines += [
        "",
        divider(),
        "",
        "<i>⚠️ Проверь доступность через @Fragment\n"
        "или Fragment.com перед использованием.</i>",
    ]

    await set_window(
        message.bot, message.from_user.id,
        "\n".join(lines),
        InlineKeyboardMarkup(inline_keyboard=[[
            _btn("🔄 Сгенерировать ещё", "menu:username"),
            _btn("🏠 Меню",              "nav:home"),
        ]]),
    )

# ═══════════════════════════════════════════════════════════
#  TELEGRAM INFO
# ═══════════════════════════════════════════════════════════
@router.message(TgInfoState.waiting)
async def tginfo_input(message: Message, state: FSMContext) -> None:
    query   = (message.text or "").strip()
    user_id = message.from_user.id
    await _del(message)
    await state.clear()

    await set_window(message.bot, user_id,
        header("📡", "TELEGRAM INFO") + "\n\n"
        f"{divider()}\n"
        "⏳ Запрашиваю данные…",
    )

    try:
        if query.startswith("@"):
            chat = await message.bot.get_chat(query)
        elif query.lstrip("-").isdigit():
            chat = await message.bot.get_chat(int(query))
        else:
            await set_window(message.bot, user_id,
                header("📡", "TELEGRAM INFO") + "\n\n"
                "⚠️  Введи <b>@username</b> или числовой <b>ID</b>.\n\n"
                f"{divider()}\n"
                "<i>✏️ Попробуй ещё раз:</i>",
                kb_cancel("nav:home"),
            )
            await state.set_state(TgInfoState.waiting)
            return

        uname = f"@{chat.username}" if chat.username else "—"
        bio   = (chat.bio or getattr(chat, "description", None) or "—")[:100]
        chat_type = {
            "private": "👤 Пользователь",
            "group":   "👥 Группа",
            "supergroup": "💬 Супергруппа",
            "channel": "📢 Канал",
        }.get(chat.type, chat.type)

        lines = [
            header("📡", "TELEGRAM INFO"),
            "",
            f"  <b>Тип:</b>      {chat_type}",
            f"  <b>Имя:</b>      {chat.full_name or chat.title or '—'}",
            f"  <b>Username:</b> {uname}",
            f"  <b>ID:</b>       <code>{chat.id}</code>",
            "",
            divider(),
            "",
            f"  <b>Bio / Описание:</b>",
            f"  <i>{bio}</i>",
        ]
        await set_window(
            message.bot, user_id,
            "\n".join(lines),
            InlineKeyboardMarkup(inline_keyboard=[[
                _btn("🔍 Ещё поиск", "menu:tginfo"),
                _btn("🏠 Меню",      "nav:home"),
            ]]),
        )
    except Exception as e:
        await set_window(
            message.bot, user_id,
            header("📡", "TELEGRAM INFO") + "\n\n"
            f"❌  <b>Не найдено</b>\n\n"
            f"{divider()}\n"
            f"<code>{str(e)[:150]}</code>\n\n"
            "<i>✏️ Попробуй ещё раз:</i>",
            kb_cancel("nav:home"),
        )
        await state.set_state(TgInfoState.waiting)

# ═══════════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════════
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
    label = f"прокси {proxy_url}" if proxy_url else "без прокси"
    logger.info("🔮 Vexis v%s запущен (%s)", VERSION, label)
    await dp.start_polling(bot)

async def main() -> None:
    if _PROXIES:
        logger.info("🔒 Прокси: %d шт.", len(_PROXIES))
        for i, proxy_url in enumerate(_PROXIES, 1):
            logger.info("  [%d/%d] Пробую %s", i, len(_PROXIES), proxy_url)
            try:
                await _start_bot(proxy_url)
                return
            except Exception as exc:
                logger.warning("  ✗ %s — %s", proxy_url, exc)
        logger.warning("⚠️  Все прокси недоступны — запускаю без прокси")

    await _start_bot(None)

if __name__ == "__main__":
    asyncio.run(main())
