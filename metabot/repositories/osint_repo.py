"""
Репозитории OSINT: источники и запросы.
"""
from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select

from metabot.models.osint import OsintSource, OsintQuery, OsintResult
from .base import BaseRepository


class OsintSourceRepository(BaseRepository[OsintSource]):
    model = OsintSource

    async def get_enabled_by_category(self, category: str) -> Sequence[OsintSource]:
        stmt = (
            select(OsintSource)
            .where(
                OsintSource.is_enabled.is_(True),
                OsintSource.category == category,
            )
            .order_by(OsintSource.sort_order)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_all_enabled(self) -> Sequence[OsintSource]:
        stmt = (
            select(OsintSource)
            .where(OsintSource.is_enabled.is_(True))
            .order_by(OsintSource.category, OsintSource.sort_order)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_slug(self, slug: str) -> Optional[OsintSource]:
        return await self.get_one(slug=slug)


class OsintQueryRepository(BaseRepository[OsintQuery]):
    model = OsintQuery


class OsintResultRepository(BaseRepository[OsintResult]):
    model = OsintResult

    async def get_by_query(self, query_id: int) -> Sequence[OsintResult]:
        stmt = (
            select(OsintResult)
            .where(OsintResult.query_id == query_id)
            .order_by(OsintResult.id)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
