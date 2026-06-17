"""
Централизованная конфигурация проекта.
Все настройки читаются из переменных окружения через Pydantic Settings.
"""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Главный класс конфигурации — один источник истины."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Telegram ──────────────────────────────────────────────
    bot_token: str
    bot_username: str = "VexisBot"

    # ── PostgreSQL ────────────────────────────────────────────
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "metabot"
    postgres_user: str = "metabot"
    postgres_password: str = "metabot"
    database_url: str = ""

    # ── Redis ─────────────────────────────────────────────────
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str = ""
    redis_url: str = ""

    # ── Owner / Admins ────────────────────────────────────────
    # Единственный владелец системы (Telegram ID). Источник истины для
    # защиты Owner (понижение/бан запрещены). Полное применение — Этап 2.
    owner_id: int = 0
    admin_ids: str = ""
    # Публичный контакт владельца для ручной оплаты/поддержки (без @).
    support_contact: str = "VEXIShelper"

    # ── Payments ──────────────────────────────────────────────
    payments_provider_token: str = ""
    enable_stars_payment: bool = True

    # ── Rate Limit ────────────────────────────────────────────
    rate_limit_per_minute: int = 30
    rate_limit_burst: int = 5

    # ── OSINT API Keys ────────────────────────────────────────
    ipinfo_token: str = ""
    whois_api_key: str = ""
    hunter_api_key: str = ""

    # ── Logging ───────────────────────────────────────────────
    log_level: str = "INFO"
    log_file: str = "logs/metabot.log"
    # "json" — структурированные логи (прод, Railway); "console" — читаемые (dev)
    log_format: str = "json"

    # ── Webhook ───────────────────────────────────────────────
    webhook_url: str = ""
    webhook_path: str = "/webhook"
    webhook_secret: str = ""
    web_server_host: str = "0.0.0.0"
    web_server_port: int = 8080

    # ── Backup ────────────────────────────────────────────────
    backup_enabled: bool = True
    backup_cron: str = "0 3 * * *"
    backup_dir: str = "/backups"

    # ── Health check ──────────────────────────────────────────
    # Внутренний HTTP-порт для liveness/readiness (Docker healthcheck,
    # Railway). Наружу не публикуется.
    health_check_enabled: bool = True
    health_check_port: int = 8081

    # ── Computed ──────────────────────────────────────────────

    @staticmethod
    def _to_asyncpg(url: str) -> str:
        """Нормализовать произвольный Postgres-URL в asyncpg-драйвер.

        Railway/Heroku отдают DATABASE_URL как ``postgres://`` или
        ``postgresql://`` (psycopg2). Для async-движка нужен
        ``postgresql+asyncpg://``. Также убираем несовместимый с asyncpg
        query-параметр ``sslmode`` (asyncpg использует ``ssl``).
        """
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]

        # Чистим sslmode из query — asyncpg его не понимает. Трогаем query
        # только если sslmode реально присутствует, и сохраняем спец-символы
        # (например, host=/socket/path), чтобы их не поломал re-encode.
        parts = urlsplit(url)
        if parts.query and "sslmode=" in parts.query:
            kept = [(k, v) for k, v in parse_qsl(parts.query) if k != "sslmode"]
            url = urlunsplit(parts._replace(query=urlencode(kept, safe="/:@")))
        return url

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self._to_asyncpg(self.database_url)
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_dsn(self) -> str:
        if self.redis_url:
            return self.redis_url
        pwd = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{pwd}{self.redis_host}:{self.redis_port}/0"

    @property
    def admin_id_list(self) -> List[int]:
        if not self.admin_ids:
            return []
        return [int(x.strip()) for x in self.admin_ids.split(",") if x.strip()]


@lru_cache()
def get_settings() -> Settings:
    """Singleton-доступ к настройкам."""
    return Settings()  # type: ignore[call-arg]
