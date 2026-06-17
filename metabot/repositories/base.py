"""
Базовый репозиторий — CRUD-операции для любой модели.
"""
from __future__ import annotations

from typing import Any, Generic, List, Optional, Sequence, Type, TypeVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """
    Обобщённый репозиторий.
    Наследники указывают `model = MyModel`.
    """

    model: Type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── CREATE ─────────────────────────────────────────────
    async def create(self, **kwargs: Any) -> ModelT:
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    # ── READ ───────────────────────────────────────────────
    async def get_by_id(self, obj_id: int) -> Optional[ModelT]:
        return await self.session.get(self.model, obj_id)

    async def get_one(self, **filters: Any) -> Optional[ModelT]:
        stmt = select(self.model).filter_by(**filters)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_many(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        order_by: Any = None,
        **filters: Any,
    ) -> Sequence[ModelT]:
        stmt = select(self.model).filter_by(**filters).offset(offset).limit(limit)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count(self, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model).filter_by(**filters)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    # ── UPDATE ─────────────────────────────────────────────
    async def update_by_id(self, obj_id: int, **values: Any) -> Optional[ModelT]:
        stmt = (
            update(self.model)
            .where(self.model.id == obj_id)  # type: ignore[attr-defined]
            .values(**values)
            .returning(self.model)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none()

    # ── DELETE ─────────────────────────────────────────────
    async def delete_by_id(self, obj_id: int) -> bool:
        stmt = delete(self.model).where(self.model.id == obj_id)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0
