"""
Репозиторий аналитики.
"""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from sqlalchemy import select

from metabot.models.analytics import AnalyticsEvent
from .base import BaseRepository


class AnalyticsRepository(BaseRepository[AnalyticsEvent]):
    model = AnalyticsEvent

    async def upsert_metric(
        self, event_date: date, metric: str, value: int, value_decimal: float | None = None
    ) -> AnalyticsEvent:
        """Обновить или создать метрику за день."""
        stmt = select(AnalyticsEvent).where(
            AnalyticsEvent.event_date == event_date,
            AnalyticsEvent.metric == metric,
        )
        result = await self.session.execute(stmt)
        entry = result.scalar_one_or_none()
        if entry:
            entry.value = value
            if value_decimal is not None:
                entry.value_decimal = value_decimal
            await self.session.flush()
            return entry
        return await self.create(
            event_date=event_date,
            metric=metric,
            value=value,
            value_decimal=value_decimal,
        )

    async def get_range(
        self, metric: str, date_from: date, date_to: date
    ) -> Sequence[AnalyticsEvent]:
        stmt = (
            select(AnalyticsEvent)
            .where(
                AnalyticsEvent.metric == metric,
                AnalyticsEvent.event_date >= date_from,
                AnalyticsEvent.event_date <= date_to,
            )
            .order_by(AnalyticsEvent.event_date)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
