"""
Репозиторий логов запросов.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import func, select

from metabot.models.request_log import RequestLog
from .base import BaseRepository


class RequestLogRepository(BaseRepository[RequestLog]):
    model = RequestLog

    async def count_by_action_since(self, action: str, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(RequestLog)
            .where(RequestLog.action == action, RequestLog.created_at >= since)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def count_total_since(self, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(RequestLog)
            .where(RequestLog.created_at >= since)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def popular_actions(self, since: datetime, limit: int = 10) -> Sequence:
        stmt = (
            select(RequestLog.action, func.count().label("cnt"))
            .where(RequestLog.created_at >= since)
            .group_by(RequestLog.action)
            .order_by(func.count().desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.all()
