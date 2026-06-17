"""
Репозиторий кэша в PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select

from metabot.models.cache_entry import CacheEntry
from .base import BaseRepository


class CacheRepository(BaseRepository[CacheEntry]):
    model = CacheEntry

    async def get_valid(self, cache_key: str) -> Optional[CacheEntry]:
        now = datetime.now(timezone.utc)
        stmt = select(CacheEntry).where(
            CacheEntry.cache_key == cache_key,
            (CacheEntry.expires_at.is_(None)) | (CacheEntry.expires_at > now),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc)
        stmt = delete(CacheEntry).where(
            CacheEntry.expires_at.is_not(None),
            CacheEntry.expires_at <= now,
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount
