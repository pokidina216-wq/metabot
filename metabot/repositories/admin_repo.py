"""
Репозиторий администраторов и аудит-логов.
"""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import select

from metabot.models.admin import Admin, AdminAction
from .base import BaseRepository


class AdminRepository(BaseRepository[Admin]):
    model = Admin

    async def get_by_telegram_id(self, telegram_id: int) -> Admin | None:
        return await self.get_one(telegram_id=telegram_id)

    async def is_admin(self, telegram_id: int) -> bool:
        return (await self.get_by_telegram_id(telegram_id)) is not None


class AdminActionRepository(BaseRepository[AdminAction]):
    model = AdminAction

    async def get_recent(self, limit: int = 50) -> Sequence[AdminAction]:
        stmt = (
            select(AdminAction)
            .order_by(AdminAction.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
