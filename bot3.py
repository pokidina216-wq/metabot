#!/usr/bin/env python3
"""
MetaBot — Telegram-бот для:
  • Извлечения метаданных из фото и файлов (JPG, PNG, PDF, MP3, …)
  • Поиска свободных тегов Telegram
  • Узнать Telegram ID по @тегу или контакту
  • Реферальной системы (+3 проверки за приглашение)
  • Экспорта метаданных в .txt файл
  • Системы подписки + Админ-панели

Запуск:
  pip install -r requirements.txt
  python bot3.py
"""

import asyncio
import io
import logging
import random
import re
import sqlite3
import string
import os
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from PIL import Image
from PIL.ExifTags import GPSTAGS, TAGS

try:
    from mutagen import File as MutaFile
    MUTAGEN_OK = True
except ImportError:
    MUTAGEN_OK = False

try:
    import pypdf
    PYPDF_OK = True
except ImportError:
    PYPDF_OK = False

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    filters,
)
from telegram.constants import ParseMode

# ══════════════════════════════════════════════════════════════════
#  ⚙️  CONFIG  — обязательно заполнить перед запуском
# ══════════════════════════════════════════════════════════════════
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")  # читается из переменных окружения Railway
ADMIN_IDS   = [int(x) for x in os.environ.get("ADMIN_IDS", "811971538").split(",") if x.strip()]
FREE_CHECKS = int(os.environ.get("FREE_CHECKS", "3"))
DB_PATH     = os.environ.get("DB_PATH", "/data/metabot.db")

PLANS: dict[str, dict] = {
    "basic":     {"days": 30,  "price": 3,   "label": "30 дней — $3"},
    "premium":   {"days": 90,  "price": 7,   "label": "90 дней — $7"},
    "unlimited": {"days": 365, "price": 12,  "label": "365 дней — $12"},
}

SUPPORT_USERNAME  = "@bulkotrias"              # куда слать оплату
BOT_USERNAME      = "your_bot_username"        # @username бота (без @) для реф-ссылки
REFERRAL_BONUS    = 3                          # бесплатных проверок за каждого приглашённого

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("metabot")

# Состояние для режима "узнать ID"
AWAIT_ID_INPUT = 1


# ══════════════════════════════════════════════════════════════════
#  🗄️  DATABASE
# ══════════════════════════════════════════════════════════════════
def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def db_init():
    with _db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id         INTEGER PRIMARY KEY,
                username        TEXT    DEFAULT '',
                first_name      TEXT    DEFAULT '',
                free_checks     INTEGER DEFAULT 3,
                sub_until       TEXT,
                total_checks    INTEGER DEFAULT 0,
                is_banned       INTEGER DEFAULT 0,
                referred_by     INTEGER DEFAULT NULL,
                referral_count  INTEGER DEFAULT 0,
                created_at      TEXT    DEFAULT (datetime('now'))
            )
        """)
        # Миграция: добавляем колонки если их нет (для существующих БД)
        for col, defn in [
            ("referred_by",    "INTEGER DEFAULT NULL"),
            ("referral_count", "INTEGER DEFAULT 0"),
        ]:
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")
            except Exception:
                pass
        c.execute("""
            CREATE TABLE IF NOT EXISTS check_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER,
                filename  TEXT,
                filetype  TEXT,
                ts        TEXT    DEFAULT (datetime('now'))
            )
        """)


def user_ensure(uid: int, username: str, first_name: str, referred_by: Optional[int] = None):
    with _db() as c:
        # Если пользователь уже есть — просто обновляем имя/ник
        existing = c.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)).fetchone()
        if existing:
            c.execute(
                "UPDATE users SET username=?, first_name=? WHERE user_id=?",
                (username, first_name, uid),
            )
        else:
            c.execute(
                "INSERT INTO users(user_id, username, first_name, referred_by) VALUES(?,?,?,?)",
                (uid, username, first_name, referred_by),
            )
            # Начисляем бонус тому, кто пригласил
            if referred_by and referred_by != uid:
                c.execute(
                    "UPDATE users SET free_checks = free_checks + ?, referral_count = referral_count + 1 "
                    "WHERE user_id=?",
                    (REFERRAL_BONUS, referred_by),
                )


def user_get(uid: int) -> Optional[sqlite3.Row]:
    with _db() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()


def user_update(uid: int, **kw):
    sets = ", ".join(f"{k}=?" for k in kw)
    vals = [*kw.values(), uid]
    with _db() as c:
        c.execute(f"UPDATE users SET {sets} WHERE user_id=?", vals)


def user_sub_active(u: sqlite3.Row) -> bool:
    if not u["sub_until"]:
        return False
    return datetime.fromisoformat(u["sub_until"]) > datetime.now()


def user_can_check(u: sqlite3.Row) -> tuple[bool, str]:
    if u["is_banned"]:
        return False, "banned"
    if user_sub_active(u):
        return True, "sub"
    if u["free_checks"] > 0:
        return True, "free"
    return False, "none"


def user_consume(uid: int, u: sqlite3.Row, fname: str, ftype: str):
    kw: dict = {"total_checks": u["total_checks"] + 1}
    if not user_sub_active(u):
        kw["free_checks"] = max(0, u["free_checks"] - 1)
    user_update(uid, **kw)
    with _db() as c:
        c.execute(
            "INSERT INTO check_log(user_id, filename, filetype) VALUES(?,?,?)",
            (uid, fname, ftype),
        )


def users_all() -> list:
    with _db() as c:
        return c.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()


def db_stats() -> dict:
    all_u = users_all()
    return {
        "total":   len(all_u),
        "subs":    sum(1 for x in all_u if user_sub_active(x)),
        "banned":  sum(1 for x in all_u if x["is_banned"]),
        "checks":  sum(x["total_checks"] for x in all_u),
        "refs":    sum(x["referral_count"] for x in all_u),
    }


def ref_link(uid: int) -> str:
    """Реферальная ссылка для пользователя."""
    return f"https://t.me/{BOT_USERNAME}?start=ref{uid}"


# ══════════════════════════════════════════════════════════════════
#  🔍  METADATA EXTRACTION
# ══════════════════════════════════════════════════════════════════
IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "tif", "heic"}
AUDIO_EXTS = {"mp3", "flac", "ogg", "m4a", "aac", "wav", "opus"}
VIDEO_EXTS = {"mp4", "mov", "avi", "mkv", "webm", "3gp"}

MAGIC_BYTES: list[tuple[str, str]] = [
    ("ffd8ff",   "JPEG изображение"),
    ("89504e47", "PNG изображение"),
    ("47494638", "GIF анимация"),
    ("25504446", "PDF документ"),
    ("504b0304", "ZIP / DOCX / XLSX / PPTX архив"),
    ("494433",   "MP3 аудио (ID3 тег)"),
    ("fffb",     "MP3 аудио"),
    ("664c6143", "FLAC аудио"),
    ("52494646", "WAV / AVI"),
    ("1a45dfa3", "MKV / WebM видео"),
    ("00000018", "MP4 видео"),
    ("00000020", "MP4 видео"),
    ("d0cf11e0", "Microsoft Office (старый формат)"),
    ("7f454c46", "ELF исполняемый файл (Linux)"),
    ("4d5a9000", "Windows PE (EXE / DLL)"),
    ("7b5c7274", "RTF документ"),
    ("38425053", "Photoshop PSD"),
]


def _gps_to_text(gps: dict) -> str:
    lines = []
    try:
        lat_raw = gps.get("GPSLatitude")
        lon_raw = gps.get("GPSLongitude")
        lat_ref = gps.get("GPSLatitudeRef", "N")
        lon_ref = gps.get("GPSLongitudeRef", "E")
        if lat_raw and lon_raw:
            def _num(x):
                if isinstance(x, (tuple, list)) and len(x) == 2:
                    return x[0] / x[1] if x[1] else 0.0
                return float(x)

            def dms(val):
                return _num(val[0]) + _num(val[1]) / 60 + _num(val[2]) / 3600
            lat = dms(lat_raw) * (-1 if lat_ref == "S" else 1)
            lon = dms(lon_raw) * (-1 if lon_ref == "W" else 1)
            lines.append(f"  📌 `{lat:.6f}, {lon:.6f}`")
            lines.append(f"  🗺 [Google Maps](https://maps.google.com/?q={lat:.6f},{lon:.6f})")
    except Exception:
        pass
    for k, v in gps.items():
        if k not in ("GPSLatitude", "GPSLongitude", "GPSLatitudeRef", "GPSLongitudeRef"):
            lines.append(f"  • {k}: {v}")
    return "\n".join(lines) if lines else "  _нет данных_"


def _fmt_val(val) -> str:
    if isinstance(val, bytes):
        try:
            val = val.decode("utf-8", errors="replace").strip("\x00").strip()
        except Exception:
            val = val.hex()[:32] + "…"
    val_str = str(val)
    if len(val_str) > 120:
        val_str = val_str[:120] + "…"
    return val_str


_TAG_HINTS = {
    "DateTimeOriginal":  "🕒 дата съёмки",
    "DateTime":          "🕒 дата изменения",
    "Make":              "🏭 производитель",
    "Model":             "📱 модель устройства",
    "Software":          "💻 ПО / редактор",
    "LensModel":         "🔭 объектив",
    "Artist":            "✍️ автор",
    "Copyright":         "© копирайт",
    "ImageDescription":  "📝 описание",
}


def meta_image(data: bytes, fname: str, source: str = "file") -> str:
    out = [f"🖼 *Изображение* — `{fname}`", "─" * 28]
    try:
        img = Image.open(io.BytesIO(data))
        out.append(f"📐 Размер: *{img.width} × {img.height} px*")
        out.append(f"🎨 Формат: {img.format or '?'}")
        out.append(f"🖌 Цвет. режим: {img.mode}")

        skip = {"MakerNote", "UserComment", "PrintImageMatching", "CFAPattern",
                "ComponentsConfiguration", "FileSource", "SceneType",
                "ExifOffset", "GPSInfo", "ExifInteroperabilityOffset"}
        found_any = False

        merged: dict[str, object] = {}
        gps: dict = {}

        try:
            exif = img.getexif()
        except Exception:
            exif = None

        if exif:
            for tid, val in exif.items():
                tag = TAGS.get(tid, str(tid))
                if tag == "GPSInfo":
                    continue
                merged.setdefault(tag, val)
            try:
                for tid, val in exif.get_ifd(0x8769).items():
                    merged.setdefault(TAGS.get(tid, str(tid)), val)
            except Exception:
                pass
            try:
                for gtid, gval in exif.get_ifd(0x8825).items():
                    gps.setdefault(GPSTAGS.get(gtid, str(gtid)), gval)
            except Exception:
                pass

        raw_exif = getattr(img, "_getexif", lambda: None)()
        if raw_exif:
            for tid, val in raw_exif.items():
                tag = TAGS.get(tid, str(tid))
                if tag == "GPSInfo" and isinstance(val, dict):
                    for gtid, gval in val.items():
                        gps.setdefault(GPSTAGS.get(gtid, str(gtid)), gval)
                    continue
                merged.setdefault(tag, val)

        if merged:
            found_any = True
            out.append("\n📋 *EXIF данные:*")
            count = 0
            for tag, val in merged.items():
                if tag in skip:
                    continue
                hint = _TAG_HINTS.get(tag, "")
                hint = f"  _({hint})_" if hint else ""
                out.append(f"  • *{tag}:* {_fmt_val(val)}{hint}")
                count += 1
                if count >= 35:
                    out.append("  _…и другие поля_")
                    break

        if gps:
            found_any = True
            out.append("\n🌍 *GPS координаты:*")
            out.append(_gps_to_text(gps))

        xmp = img.info.get("xmp")
        if xmp:
            found_any = True
            try:
                xmp_txt = xmp.decode("utf-8", errors="replace") if isinstance(xmp, bytes) else str(xmp)
            except Exception:
                xmp_txt = str(xmp)
            tags = re.findall(r"<(?:[\w]+:)?(\w+)>([^<]{1,120})</", xmp_txt)
            if tags:
                out.append("\n🧬 *XMP:*")
                for k, v in tags[:12]:
                    v = v.strip()
                    if v:
                        out.append(f"  • *{k}:* {v}")

        try:
            from PIL import IptcImagePlugin
            iptc = IptcImagePlugin.getiptcinfo(img)
            if iptc:
                found_any = True
                out.append("\n🗞 *IPTC:*")
                for k, v in list(iptc.items())[:12]:
                    out.append(f"  • {k}: {_fmt_val(v)}")
        except Exception:
            pass

        if img.info.get("icc_profile"):
            out.append(f"\n🎨 ICC-профиль: есть ({len(img.info['icc_profile']):,} байт)")

        if not found_any:
            out.append("\n⚠️ *Метаданные не найдены*")
            if source == "photo":
                out.append(
                    "\n❗️*Причина:* вы отправили изображение как *«Фото»* — "
                    "Telegram сжимает картинку и *вырезает все метаданные* "
                    "(EXIF, GPS, дату, модель) ещё до получения ботом.\n\n"
                    "✅ *Решение:* отправьте то же фото как *файл/документ* "
                    "(📎 → «Отправить как файл» / «Send as file»)."
                )
            else:
                out.append("_Метаданные были удалены ранее или не записывались устройством._")
    except Exception as e:
        out.append(f"\n❌ Ошибка разбора: {e}")
    return "\n".join(out)


def meta_audio(data: bytes, fname: str) -> str:
    out = [f"🎵 *Аудио* — `{fname}`", "─" * 28, f"📦 Размер: {len(data):,} байт"]
    if not MUTAGEN_OK:
        out.append("⚠️ Установите mutagen для полного анализа аудио")
        return "\n".join(out)
    try:
        af = MutaFile(io.BytesIO(data), filename=fname)
        if af:
            info = getattr(af, "info", None)
            if info:
                if hasattr(info, "length"):
                    m, s = divmod(int(info.length), 60)
                    out.append(f"⏱ Длительность: *{m}:{s:02d}*")
                if hasattr(info, "bitrate"):
                    out.append(f"📡 Битрейт: {info.bitrate // 1000} кбит/с")
                if hasattr(info, "sample_rate"):
                    out.append(f"🔊 Частота: {info.sample_rate} Гц")
                if hasattr(info, "channels"):
                    out.append(f"🔈 Каналов: {info.channels}")
            if af.tags:
                out.append("\n🏷 *Теги:*")
                for k, v in list(af.tags.items())[:25]:
                    out.append(f"  • *{k}:* {str(v)[:90]}")
        else:
            out.append("⚠️ Теги не найдены")
    except Exception as e:
        out.append(f"❌ Ошибка: {e}")
    return "\n".join(out)


def meta_pdf(data: bytes, fname: str) -> str:
    out = [f"📄 *PDF документ* — `{fname}`", "─" * 28, f"📦 Размер: {len(data):,} байт"]
    if not PYPDF_OK:
        out.append("⚠️ Установите pypdf для разбора PDF")
        return "\n".join(out)
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        out.append(f"📃 Страниц: *{len(reader.pages)}*")
        out.append(f"🔒 Зашифрован: {'Да' if reader.is_encrypted else 'Нет'}")
        meta = reader.metadata
        if meta:
            out.append("\n📋 *Метаданные:*")
            for k, v in meta.items():
                if v:
                    out.append(f"  • *{str(k).lstrip('/')}:* {str(v)[:100]}")
    except Exception as e:
        out.append(f"❌ Ошибка: {e}")
    return "\n".join(out)


def meta_generic(data: bytes, fname: str) -> str:
    ext = fname.rsplit(".", 1)[-1].upper() if "." in fname else "?"
    sig = data[:8].hex()
    detected = "Неизвестен"
    for prefix, name in MAGIC_BYTES:
        if sig.startswith(prefix):
            detected = name
            break

    out = [
        f"📁 *Файл* — `{fname}`",
        "─" * 28,
        f"🔤 Расширение: {ext}",
        f"📦 Размер: {len(data):,} байт  ({len(data)/1024:.1f} КБ)",
        f"🔑 Сигнатура: `{data[:8].hex()}`",
        f"🔍 Определён как: *{detected}*",
    ]
    return "\n".join(out)


def extract_metadata(data: bytes, fname: str, source: str = "file") -> str:
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if ext in IMAGE_EXTS:
        return meta_image(data, fname, source)
    if ext in AUDIO_EXTS:
        return meta_audio(data, fname)
    if ext == "pdf":
        return meta_pdf(data, fname)
    return meta_generic(data, fname)


# ══════════════════════════════════════════════════════════════════
#  🔎  TELEGRAM USERNAME FINDER
# ══════════════════════════════════════════════════════════════════
def _gen_beautiful(length: int) -> str:
    alpha = string.ascii_lowercase
    strategy = random.choice(["palindrome", "repeat", "syllable", "double"])

    if strategy == "palindrome":
        half = (length + 1) // 2
        base = "".join(random.choices(alpha, k=half))
        s = base + base[-2::-1]
        return s[:length]

    if strategy == "repeat":
        unit_len = random.randint(1, max(1, length // 3))
        unit = "".join(random.choices(alpha, k=unit_len))
        return (unit * (length // unit_len + 2))[:length]

    if strategy == "double":
        half = max(1, length // 2)
        base = "".join(random.choices(alpha, k=half))
        return (base * 2)[:length]

    # syllable: CVCVCV…
    vowels = "aeiou"
    cons = "bcdfghjklmnprstvwxyz"
    s = ""
    for i in range(length):
        s += random.choice(cons if i % 2 == 0 else vowels)
    return s


def _gen_any(length: int) -> str:
    chars = string.ascii_lowercase + string.digits
    return random.choice(string.ascii_lowercase) + \
           "".join(random.choices(chars, k=length - 1))


def _gen_candidates(length: int, beautiful: bool, n: int = 150) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    attempts = 0
    while len(out) < n and attempts < n * 20:
        attempts += 1
        s = (_gen_beautiful(length) if beautiful else _gen_any(length))[:length]
        if (
            len(s) == length
            and s[0].isalpha()
            and re.fullmatch(r"[a-zA-Z0-9_]+", s)
            and s not in seen
        ):
            seen.add(s)
            out.append(s)
    return out


async def _tg_is_free(username: str, sess: aiohttp.ClientSession) -> bool:
    """True — если ник, скорее всего, свободен."""
    try:
        async with sess.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getChat",
            params={"chat_id": f"@{username}"},
            timeout=aiohttp.ClientTimeout(total=7),
        ) as r:
            data = await r.json()
            if data.get("ok"):
                return False   # занят
            desc = (data.get("description") or "").lower()
            if "chat not found" in desc:
                return await _tg_is_free_web(username, sess)
            return False
    except Exception:
        return False


async def _tg_is_free_web(username: str, sess: aiohttp.ClientSession) -> bool:
    try:
        async with sess.get(
            f"https://t.me/{username}",
            timeout=aiohttp.ClientTimeout(total=7),
            allow_redirects=True,
        ) as r:
            if r.status == 404:
                return True
            body = await r.text()
            if "tgme_page_photo" in body or "tgme_page_title" in body:
                return False
            if "fragment.com" in body or "is for sale" in body.lower():
                return False
            return True
    except Exception:
        return False


async def find_free_usernames(length: int, beautiful: bool, want: int = 10) -> list[str]:
    candidates = _gen_candidates(length, beautiful, want * 20)
    found: list[str] = []
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; MetaBot/1.0)"}
    async with aiohttp.ClientSession(headers=hdrs) as sess:
        for i in range(0, len(candidates), 25):
            chunk = candidates[i: i + 25]
            results = await asyncio.gather(
                *[_tg_is_free(u, sess) for u in chunk],
                return_exceptions=True,
            )
            for u, ok in zip(chunk, results):
                if ok is True:
                    found.append(u)
                    if len(found) >= want:
                        return found
    return found


# ══════════════════════════════════════════════════════════════════
#  🪪  TELEGRAM ID LOOKUP
# ══════════════════════════════════════════════════════════════════
async def resolve_username_to_id_by_id(user_id: int, bot) -> dict | None:
    """Получить имя/тег по числовому user_id через getChat."""
    try:
        chat = await bot.get_chat(user_id)
        name_parts = []
        if getattr(chat, "first_name", None):
            name_parts.append(chat.first_name)
        if getattr(chat, "last_name", None):
            name_parts.append(chat.last_name)
        if getattr(chat, "title", None):
            name_parts.append(chat.title)
        name = " ".join(name_parts) or "—"
        type_labels = {
            "private":    "👤 Пользователь",
            "group":      "👥 Группа",
            "supergroup": "🏛 Супергруппа",
            "channel":    "📢 Канал",
        }
        return {
            "id":       chat.id,
            "type":     type_labels.get(chat.type, chat.type),
            "name":     name,
            "username": chat.username or "",
        }
    except Exception:
        return None


async def resolve_username_to_id(username: str, bot) -> dict | None:
    """
    Пытается получить ID по @username через getChat.
    Возвращает dict с ключами: id, type, name, username — или None.
    """
    try:
        chat = await bot.get_chat(f"@{username}")
        name_parts = []
        if getattr(chat, "first_name", None):
            name_parts.append(chat.first_name)
        if getattr(chat, "last_name", None):
            name_parts.append(chat.last_name)
        if getattr(chat, "title", None):
            name_parts.append(chat.title)
        name = " ".join(name_parts) or "—"

        type_labels = {
            "private":    "👤 Пользователь",
            "group":      "👥 Группа",
            "supergroup": "🏛 Супергруппа",
            "channel":    "📢 Канал",
        }
        chat_type = type_labels.get(chat.type, chat.type)

        return {
            "id":       chat.id,
            "type":     chat_type,
            "name":     name,
            "username": chat.username or "",
        }
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
#  ⌨️  KEYBOARDS
# ══════════════════════════════════════════════════════════════════
def kb_main(admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔍  Метаданные файла / фото", callback_data="meta")],
        [InlineKeyboardButton("🔎  Найти свободный тег",      callback_data="tag")],
        [InlineKeyboardButton("🪪  Узнать Telegram ID",       callback_data="getid")],
        [InlineKeyboardButton("🎁  Реферальная программа",    callback_data="referral")],
        [
            InlineKeyboardButton("👤 Профиль",   callback_data="profile"),
            InlineKeyboardButton("💎 Подписка",  callback_data="plans"),
        ],
    ]
    if admin:
        rows.append([InlineKeyboardButton("⚙️  Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


def kb_back(to: str = "home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️  Назад", callback_data=to)]])


def kb_plans() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(v["label"], callback_data=f"buy:{k}")] for k, v in PLANS.items()]
    rows.append([InlineKeyboardButton("◀️  Назад", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def kb_tag_len() -> InlineKeyboardMarkup:
    rows: list[list] = []
    row: list = []
    for i in range(5, 13):
        row.append(InlineKeyboardButton(str(i), callback_data=f"tlen:{i}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("◀️  Назад", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def kb_tag_type(ln: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨  Красивые (палиндром / повтор)", callback_data=f"ttype:beautiful:{ln}")],
        [InlineKeyboardButton("🎲  Случайные (любые символы)",     callback_data=f"ttype:any:{ln}")],
        [InlineKeyboardButton("◀️  Назад", callback_data="tag")],
    ])


def kb_tag_count(tp: str, ln: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("5",  callback_data=f"tcount:5:{tp}:{ln}"),
            InlineKeyboardButton("10", callback_data=f"tcount:10:{tp}:{ln}"),
            InlineKeyboardButton("20", callback_data=f"tcount:20:{tp}:{ln}"),
        ],
        [InlineKeyboardButton("◀️  Назад", callback_data=f"tlen:{ln}")],
    ])


def kb_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊  Статистика",           callback_data="adm:stats")],
        [InlineKeyboardButton("👑  Выдать подписку",      callback_data="adm:give")],
        [InlineKeyboardButton("🚫  Забрать подписку",     callback_data="adm:revoke")],
        [InlineKeyboardButton("🔇  Заблокировать юзера",  callback_data="adm:ban")],
        [InlineKeyboardButton("📋  Список пользователей", callback_data="adm:users")],
        [InlineKeyboardButton("◀️  Главное меню",         callback_data="home")],
    ])





# ══════════════════════════════════════════════════════════════════
#  🛠  HELPERS
# ══════════════════════════════════════════════════════════════════
def _sub_line(u: sqlite3.Row) -> str:
    if user_sub_active(u):
        until = datetime.fromisoformat(u["sub_until"]).strftime("%d.%m.%Y")
        return f"✅ Подписка активна до *{until}*"
    return f"🆓 Бесплатных проверок: *{u['free_checks']}* / {FREE_CHECKS}"


async def _reply(update: Update, text: str, reply_markup=None, disable_preview: bool = True):
    kw = dict(
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=disable_preview,
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(**kw)
    else:
        await update.message.reply_text(**kw)


# ══════════════════════════════════════════════════════════════════
#  📨  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user

    # Парсим реферальный параметр: /start ref123456789
    referred_by: Optional[int] = None
    is_new_user = user_get(tg.id) is None
    if ctx.args:
        arg = ctx.args[0]
        if arg.startswith("ref"):
            try:
                ref_uid = int(arg[3:])
                if ref_uid != tg.id:
                    referred_by = ref_uid
            except ValueError:
                pass

    user_ensure(tg.id, tg.username or "", tg.first_name or "", referred_by)
    u = user_get(tg.id)

    # Уведомляем реферера о новом приглашённом
    if is_new_user and referred_by:
        try:
            await ctx.bot.send_message(
                referred_by,
                f"🎉 *Новый реферал!*\n\n"
                f"Пользователь *{tg.first_name}* зарегистрировался по вашей ссылке.\n"
                f"Вам начислено *+{REFERRAL_BONUS}* бесплатных проверок! 🎁",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    if u and u["is_banned"]:
        await _reply(update, "🚫 Вы заблокированы.")
        return

    is_admin = tg.id in ADMIN_IDS
    welcome = f"👋 С возвращением, *{tg.first_name}*!" if not is_new_user else f"👋 Привет, *{tg.first_name}*! Добро пожаловать!"
    text = (
        f"{welcome}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🤖 *MetaBot* — ваш инструмент для:\n\n"
        f"  🔍 Анализа метаданных фото и файлов\n"
        f"  🔎 Поиска свободных Telegram-тегов\n"
        f"  🪪 Узнать Telegram ID пользователя\n"
        f"  🎁 Приглашай друзей — получай бонусы\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{_sub_line(u)}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"Выберите действие:"
    )
    await _reply(update, text, kb_main(is_admin))


async def cmd_give_sub(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not ctx.args or len(ctx.args) != 2:
        await update.message.reply_text("Формат: `/give_sub USER_ID DAYS`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid, days = int(ctx.args[0]), int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ USER\\_ID и DAYS должны быть числами.", parse_mode=ParseMode.MARKDOWN)
        return
    u = user_get(uid)
    if not u:
        await update.message.reply_text("❌ Пользователь не найден.")
        return
    until = datetime.now() + timedelta(days=days)
    user_update(uid, sub_until=until.isoformat())
    await update.message.reply_text(
        f"✅ Подписка выдана!\n👤 ID: `{uid}`\n📅 До: {until.strftime('%d.%m.%Y')}",
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        await ctx.bot.send_message(
            uid,
            f"🎉 Вам выдана подписка на *{days}* дней!\n"
            f"Активна до: *{until.strftime('%d.%m.%Y')}*",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass


async def cmd_revoke_sub(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not ctx.args:
        await update.message.reply_text("Формат: `/revoke_sub USER_ID`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ Неверный USER_ID.")
        return
    user_update(uid, sub_until=None)
    await update.message.reply_text(f"✅ Подписка отозвана у `{uid}`.", parse_mode=ParseMode.MARKDOWN)


async def cmd_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not ctx.args:
        await update.message.reply_text("Формат: `/ban USER_ID`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ Неверный USER_ID.")
        return
    user_update(uid, is_banned=1)
    await update.message.reply_text(f"🚫 Пользователь `{uid}` заблокирован.", parse_mode=ParseMode.MARKDOWN)


async def cmd_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not ctx.args:
        await update.message.reply_text("Формат: `/unban USER_ID`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        uid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ Неверный USER_ID.")
        return
    user_update(uid, is_banned=0)
    await update.message.reply_text(f"✅ Пользователь `{uid}` разблокирован.", parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════
#  🔘  CALLBACK QUERY HANDLER
# ══════════════════════════════════════════════════════════════════
async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid  = q.from_user.id
    is_admin = uid in ADMIN_IDS

    user_ensure(uid, q.from_user.username or "", q.from_user.first_name or "")
    u = user_get(uid)

    if u and u["is_banned"] and data != "home":
        await q.edit_message_text("🚫 Вы заблокированы.")
        return

    # ── HOME ─────────────────────────────────────────────────────
    if data == "home":
        await cmd_start(update, ctx)

    # ── META ─────────────────────────────────────────────────────
    elif data == "meta":
        can, reason = user_can_check(u)
        if not can:
            await q.edit_message_text(
                "❌ *Бесплатные попытки исчерпаны!*\n\nОформите подписку для продолжения.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💎 Оформить подписку", callback_data="plans")],
                    [InlineKeyboardButton("◀️ Назад", callback_data="home")],
                ]),
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        hint = f"\n⚠️ Осталось бесплатных: *{u['free_checks']}*" if reason == "free" else ""
        ctx.user_data["expect_file"] = True
        await q.edit_message_text(
            f"📎 *Отправьте фото или файл*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Я покажу все метаданные.{hint}\n\n"
            f"❗️*Важно для фото:* отправляйте картинку как *файл/документ* "
            f"(📎 → «Отправить как файл»). При отправке как «Фото» Telegram "
            f"сжимает снимок и *удаляет все метаданные* (EXIF, GPS и т.д.).\n\n"
            f"_Поддерживаются: JPG, PNG, GIF, PDF, MP3, FLAC, WAV, M4A и другие._",
            reply_markup=kb_back("home"),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── EXPORT META ──────────────────────────────────────────────
    elif data == "export_meta":
        meta_text = ctx.user_data.get("last_meta_result")
        meta_fname = ctx.user_data.get("last_meta_fname", "metadata")
        if not meta_text:
            await q.answer("⚠️ Нет данных для экспорта. Сначала отправьте файл.", show_alert=True)
            return
        # Убираем Markdown-разметку для чистого текстового файла
        clean = re.sub(r"[*_`]", "", meta_text)
        export_name = f"meta_{meta_fname.rsplit('.', 1)[0]}.txt"
        file_bytes = io.BytesIO(clean.encode("utf-8"))
        file_bytes.name = export_name
        await q.answer("📄 Готовлю файл…")
        await q.message.reply_document(
            document=file_bytes,
            filename=export_name,
            caption=(
                f"📄 *Метаданные:* `{meta_fname}`\n"
                f"_Экспортировано через MetaBot_"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── REFERRAL ─────────────────────────────────────────────────
    elif data == "referral":
        link = ref_link(uid)
        await q.edit_message_text(
            f"🎁 *Реферальная программа*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Приглашай друзей — получай бонусы!\n\n"
            f"📌 *Как это работает:*\n"
            f"  1️⃣ Отправь другу свою реферальную ссылку\n"
            f"  2️⃣ Друг нажимает на ссылку и запускает бота\n"
            f"  3️⃣ Тебе сразу начисляется *+{REFERRAL_BONUS} бесплатных проверки*\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔗 *Твоя ссылка:*\n"
            f"`{link}`\n\n"
            f"👥 Приглашено друзей: *{u['referral_count']}*\n"
            f"🎁 Бонусов получено: *{u['referral_count'] * REFERRAL_BONUS}* проверки\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"_Нажми на ссылку чтобы скопировать, затем отправь другу._",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Поделиться ссылкой", url=f"https://t.me/share/url?url={link}&text=🤖 Попробуй этого бота — анализ метаданных, поиск тегов и многое другое!")],
                [InlineKeyboardButton("◀️ Назад", callback_data="home")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── GET ID — начало ──────────────────────────────────────────
    elif data == "getid":
        ctx.user_data["expect_id"] = True
        await q.edit_message_text(
            "🪪 *Узнать Telegram ID*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Отправьте *@username* пользователя, канала или группы.\n\n"
            "_Бот найдёт Telegram ID по этому тегу._",
            reply_markup=kb_back("home"),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── PROFILE ──────────────────────────────────────────────────
    elif data == "profile":
        created = datetime.fromisoformat(u["created_at"]).strftime("%d.%m.%Y")
        await q.edit_message_text(
            f"👤 *Профиль*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 Ваш ID: `{uid}`\n"
            f"📅 В боте с: {created}\n"
            f"🔍 Всего проверок: *{u['total_checks']}*\n\n"
            f"{_sub_line(u)}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Оформить подписку", callback_data="plans")],
                [InlineKeyboardButton("◀️ Назад", callback_data="home")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── PLANS ────────────────────────────────────────────────────
    elif data == "plans":
        await q.edit_message_text(
            "💎 *Тарифы*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Полный доступ ко всем функциям бота на выбранный период.\n"
            "Все планы одинаковые — разница только в количестве дней.\n\n"
            "Выберите план:",
            reply_markup=kb_plans(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data.startswith("buy:"):
        key = data.split(":", 1)[1]
        plan = PLANS.get(key)
        if plan:
            await q.edit_message_text(
                f"💳 *{plan['label']}*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"Напишите для оплаты: {SUPPORT_USERNAME}\n\n"
                f"Укажите ваш Telegram ID\n\n"
                f"_Подписка активируется в течение нескольких минут после подтверждения оплаты._",
                reply_markup=kb_back("plans"),
                parse_mode=ParseMode.MARKDOWN,
            )

    # ── TAG — выбор длины ────────────────────────────────────────
    elif data == "tag":
        await q.edit_message_text(
            "🔎 *Поиск свободных тегов Telegram*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Выберите длину тега (количество символов):",
            reply_markup=kb_tag_len(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data.startswith("tlen:"):
        ln = int(data.split(":")[1])
        await q.edit_message_text(
            f"🔤 Длина тега: *{ln}* символов\n\nКакой тип ника ищем?",
            reply_markup=kb_tag_type(ln),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data.startswith("ttype:"):
        _, tp, ln_str = data.split(":")
        ln = int(ln_str)
        label = "✨ Красивые" if tp == "beautiful" else "🎲 Случайные"
        await q.edit_message_text(
            f"🔤 Длина: *{ln}* | Тип: *{label}*\n\nСколько тегов найти?",
            reply_markup=kb_tag_count(tp, ln),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data.startswith("tcount:"):
        _, cnt_s, tp, ln_s = data.split(":")
        cnt, ln = int(cnt_s), int(ln_s)
        beautiful = (tp == "beautiful")
        lbl = "красивых ✨" if beautiful else "случайных 🎲"

        await q.edit_message_text(
            f"⏳ Ищу *{cnt}* {lbl} тегов длиной *{ln}* символов…\n"
            f"Это займёт несколько секунд ⏱",
            parse_mode=ParseMode.MARKDOWN,
        )

        found = await find_free_usernames(ln, beautiful, cnt)

        if found:
            tags_block = "\n".join(f"  ✅ @{t}" for t in found)
            text = (
                f"🎉 *Найдено {len(found)} свободных тегов:*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"{tags_block}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚡️ Регистрируйте быстро — теги могут быть заняты в любой момент!"
            )
        else:
            text = (
                "😔 *Свободных тегов не найдено*\n\n"
                "Попробуйте другую длину или тип.\n"
                "_Иногда требуется несколько попыток._"
            )

        await q.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Искать снова", callback_data=data)],
                [InlineKeyboardButton("◀️ Параметры",    callback_data="tag")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="home")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )

    # ── ADMIN ─────────────────────────────────────────────────────
    elif data == "admin" and is_admin:
        st = db_stats()
        await q.edit_message_text(
            f"⚙️ *Админ-панель*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Пользователей: *{st['total']}*\n"
            f"💎 Подписчиков: *{st['subs']}*\n"
            f"🔍 Всего проверок: *{st['checks']}*",
            reply_markup=kb_admin(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "adm:stats" and is_admin:
        st = db_stats()
        await q.edit_message_text(
            f"📊 *Статистика*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Пользователей: *{st['total']}*\n"
            f"💎 Активных подписок: *{st['subs']}*\n"
            f"🆓 Без подписки: *{st['total'] - st['subs']}*\n"
            f"🚫 Заблокировано: *{st['banned']}*\n"
            f"🔍 Всего проверок: *{st['checks']}*\n"
            f"🎁 Рефералов привлечено: *{st['refs']}*",
            reply_markup=kb_back("admin"),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "adm:give" and is_admin:
        await q.edit_message_text(
            "👑 *Выдать подписку*\n\nКоманда:\n`/give_sub USER_ID DAYS`\n\nПример:\n`/give_sub 123456789 30`",
            reply_markup=kb_back("admin"),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "adm:revoke" and is_admin:
        await q.edit_message_text(
            "🚫 *Забрать подписку*\n\nКоманда:\n`/revoke_sub USER_ID`",
            reply_markup=kb_back("admin"),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "adm:ban" and is_admin:
        await q.edit_message_text(
            "🔇 *Бан / разбан*\n\n"
            "Заблокировать: `/ban USER_ID`\n"
            "Разблокировать: `/unban USER_ID`",
            reply_markup=kb_back("admin"),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "adm:users" and is_admin:
        all_u = users_all()[:15]
        lines = []
        for x in all_u:
            icon = "💎" if user_sub_active(x) else ("🚫" if x["is_banned"] else "🆓")
            name = (x["first_name"] or x["username"] or "—")[:20]
            lines.append(f"{icon} {name}  |  `{x['user_id']}`  |  ×{x['total_checks']}")
        await q.edit_message_text(
            "📋 *Пользователи (посл. 15):*\n\n" + "\n".join(lines),
            reply_markup=kb_back("admin"),
            parse_mode=ParseMode.MARKDOWN,
        )


# ══════════════════════════════════════════════════════════════════
#  📁  FILE MESSAGE HANDLER
# ══════════════════════════════════════════════════════════════════
async def on_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_ensure(uid, update.effective_user.username or "", update.effective_user.first_name or "")
    u = user_get(uid)

    if u and u["is_banned"]:
        await update.message.reply_text("🚫 Вы заблокированы.")
        return

    can, reason = user_can_check(u)
    if not can:
        await update.message.reply_text(
            "❌ *Бесплатные попытки исчерпаны!*\nОформите подписку.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Подписка", callback_data="plans")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    status = await update.message.reply_text("⏳ Анализирую файл…")

    try:
        source = "file"
        if update.message.photo:
            ph = update.message.photo[-1]
            tg_file = await ctx.bot.get_file(ph.file_id)
            fname = f"photo_{ph.file_id[:8]}.jpg"
            ftype = "jpg"
            source = "photo"
        elif update.message.document:
            doc = update.message.document
            tg_file = await ctx.bot.get_file(doc.file_id)
            fname = doc.file_name or "document"
            ftype = fname.rsplit(".", 1)[-1].lower() if "." in fname else "bin"
        else:
            await status.edit_text("❌ Поддерживаются только фото и файлы.")
            return

        raw = bytes(await tg_file.download_as_bytearray())
        result = extract_metadata(raw, fname, source)
        user_consume(uid, u, fname, ftype)
        u = user_get(uid)

        footer = (
            "\n\n💎 _Подписка активна_"
            if user_sub_active(u)
            else f"\n\n🆓 _Осталось бесплатных: {u['free_checks']} / {FREE_CHECKS}_"
        )

        # Сохраняем результат в user_data для возможного экспорта
        ctx.user_data["last_meta_result"] = result
        ctx.user_data["last_meta_fname"]  = fname

        await status.edit_text(
            result + footer,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 Экспорт в .txt", callback_data="export_meta")],
                [InlineKeyboardButton("🔍 Проверить ещё",  callback_data="meta")],
                [InlineKeyboardButton("🏠 Главное меню",   callback_data="home")],
            ]),
            disable_web_page_preview=True,
        )

    except Exception as e:
        log.exception("on_file error")
        await status.edit_text(f"❌ Ошибка при обработке файла:\n`{e}`", parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════════════════════════
#  📨  TEXT MESSAGE HANDLER (username для getid + всё остальное)
# ══════════════════════════════════════════════════════════════════
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовые сообщения — в первую очередь @username для ID-поиска."""
    uid = update.effective_user.id
    user_ensure(uid, update.effective_user.username or "", update.effective_user.first_name or "")
    u = user_get(uid)

    if u and u["is_banned"]:
        return

    text = (update.message.text or "").strip()

    # Если пользователь в режиме ожидания ID-запроса
    if ctx.user_data.get("expect_id"):
        # Убираем @ если есть
        username = text.lstrip("@").strip()

        # Валидация: только латиница, цифры, _; длина 5-32
        if not re.fullmatch(r"[a-zA-Z0-9_]{4,32}", username):
            await update.message.reply_text(
                "❌ Неверный формат username.\n\n"
                "Введите корректный @тег (только латиница, цифры, _) "
                "или воспользуйтесь кнопкой контакта.",
                reply_markup=kb_getid_contact(),
            )
            return

        status = await update.message.reply_text(f"🔍 Ищу @{username}…")

        result = await resolve_username_to_id(username, ctx.bot)

        if result:
            ctx.user_data.pop("expect_id", None)
            await status.edit_text(
                f"🪪 *Результат поиска*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"🔖 Тег: @{result['username'] or username}\n"
                f"🆔 ID: `{result['id']}`\n"
                f"📛 Имя: {result['name']}\n"
                f"📂 Тип: {result['type']}\n\n"
                f"_Скопируйте ID нажав на него._",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔍 Найти другого", callback_data="getid")],
                    [InlineKeyboardButton("🏠 Главное меню",  callback_data="home")],
                ]),
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await status.edit_text(
                f"😔 *Пользователь не найден*\n\n"
                f"Тег `@{username}` не существует или профиль скрыт.\n\n"
                f"*Возможные причины:*\n"
                f"  • Аккаунт удалён или не зарегистрирован\n"
                f"  • Опечатка в username\n"
                f"  • Аккаунт приватный без публичного тега\n\n"
                f"Попробуйте другой тег или поделитесь контактом 👇",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="home")],
                ]),
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # Обычное сообщение — подсказка
    await update.message.reply_text(
        "👆 Используйте кнопки меню или отправьте /start",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Главное меню", callback_data="home")],
        ]),
    )





# ══════════════════════════════════════════════════════════════════
#  🚀  MAIN
# ══════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKEN:
        raise RuntimeError("Установите переменную окружения BOT_TOKEN!")

    db_init()
    log.info("База данных инициализирована: %s", DB_PATH)

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("give_sub",   cmd_give_sub))
    app.add_handler(CommandHandler("revoke_sub", cmd_revoke_sub))
    app.add_handler(CommandHandler("ban",        cmd_ban))
    app.add_handler(CommandHandler("unban",      cmd_unban))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(on_button))

    # Files & photos
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, on_file))

    # Text messages (last — чтобы не перехватывать файлы)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("MetaBot запущен. Ctrl+C для остановки.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
