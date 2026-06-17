"""
Валидация и санитизация пользовательского ввода.
"""
from __future__ import annotations

import re
from html import escape as html_escape


# ── Регулярные выражения ──────────────────────────────────────
_PHONE_RE = re.compile(r"^\+?\d{7,15}$")
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$", re.ASCII)
_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$")
_IP_RE = re.compile(
    r"^((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(25[0-5]|2[0-4]\d|[01]?\d\d?)$"
)
_NICK_RE = re.compile(r"^[\w.]{1,64}$", re.ASCII)
_TG_USERNAME_RE = re.compile(r"^@?[a-zA-Z][a-zA-Z0-9_]{3,31}$")

# Макс. длина пользовательского ввода для OSINT-запроса
MAX_QUERY_LEN = 256


def sanitize(text: str | None, max_len: int = 256) -> str:
    """Обрезать и экранировать HTML-спецсимволы."""
    if not text:
        return ""
    return html_escape(text.strip()[:max_len])


def validate_phone(value: str) -> str | None:
    """Нормализовать и проверить телефон. None если невалиден."""
    clean = re.sub(r"[\s()\-]", "", value.strip())
    if _PHONE_RE.match(clean):
        return clean
    return None


def validate_email(value: str) -> str | None:
    v = value.strip().lower()
    if len(v) <= 254 and _EMAIL_RE.match(v):
        return v
    return None


def validate_domain(value: str) -> str | None:
    v = value.strip().lower().rstrip(".")
    if _DOMAIN_RE.match(v):
        return v
    return None


def validate_ip(value: str) -> str | None:
    v = value.strip()
    if _IP_RE.match(v):
        return v
    return None


def validate_nick(value: str) -> str | None:
    v = value.strip().lstrip("@")
    if 1 <= len(v) <= 64 and _NICK_RE.match(v):
        return v
    return None


def validate_fullname(value: str) -> str | None:
    v = value.strip()
    if 2 <= len(v) <= 128:
        return v
    return None


def validate_tg_username(value: str) -> str | None:
    v = value.strip()
    if _TG_USERNAME_RE.match(v):
        return v if v.startswith("@") else f"@{v}"
    return None


# Маппинг категория → валидатор
OSINT_VALIDATORS: dict[str, callable] = {
    "phone": validate_phone,
    "email": validate_email,
    "domain": validate_domain,
    "ip": validate_ip,
    "nick": validate_nick,
    "fullname": validate_fullname,
}


def validate_osint_query(category: str, raw: str) -> tuple[str | None, str]:
    """Проверить OSINT-запрос. Возвращает (validated, error_message).
    validated=None если невалиден."""
    if not raw or len(raw) > MAX_QUERY_LEN:
        return None, "Запрос пустой или слишком длинный."
    validator = OSINT_VALIDATORS.get(category)
    if not validator:
        # Неизвестная категория → пропустить как есть
        return raw.strip()[:MAX_QUERY_LEN], ""
    result = validator(raw)
    if result is None:
        hints = {
            "phone": "Пример: <code>+79001234567</code>",
            "email": "Пример: <code>user@example.com</code>",
            "domain": "Пример: <code>example.com</code>",
            "ip": "Пример: <code>8.8.8.8</code>",
            "nick": "Пример: <code>cooluser42</code>",
            "fullname": "Пример: <code>Иванов Иван</code>",
        }
        return None, f"⚠️ Неверный формат.\n{hints.get(category, '')}"
    return result, ""
