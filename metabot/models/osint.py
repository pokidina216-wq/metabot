"""
OSINT-модели: источники, запросы и результаты.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import (
    Boolean, ForeignKey, Index, Integer, String, Text, JSON
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class OsintSource(TimestampMixin, Base):
    """
    Источник OSINT-данных.
    Администратор может добавлять через админ-панель.
    """

    __tablename__ = "osint_sources"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Тип источника
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # api / scraper / whois / dns / custom

    # Категория поиска
    category: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # phone / email / username / nick / fullname / domain / ip / face

    # Конфигурация подключения (хранится как JSON)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Пример: {"url": "https://api.example.com/lookup", "method": "GET",
    #          "headers": {"X-Api-Key": "..."}, "params_template": {"q": "{query}"},
    #          "response_path": "data.results"}

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_osint_sources_category", "category"),
        Index("ix_osint_sources_enabled", "is_enabled"),
    )


class OsintQuery(TimestampMixin, Base):
    """Запрос пользователя в OSINT-модуль."""

    __tablename__ = "osint_queries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    query_type: Mapped[str] = mapped_column(String(32), nullable=False)  # phone / email / ...
    query_value: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending / processing / done / error

    __table_args__ = (
        Index("ix_osint_queries_user_type", "user_id", "query_type"),
    )


class OsintResult(TimestampMixin, Base):
    """Результат OSINT-проверки."""

    __tablename__ = "osint_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    query_id: Mapped[int] = mapped_column(
        ForeignKey("osint_queries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("osint_sources.id", ondelete="CASCADE"), nullable=False
    )

    raw_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    formatted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_found: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_osint_results_query", "query_id"),
    )
