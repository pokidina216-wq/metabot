"""
Репозиторий пользователей.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import func, select, update

from metabot.models.user import User, UserRole
from .base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        return await self.get_one(telegram_id=telegram_id)

    async def get_or_create(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language_code: str | None = None,
        referral_code: str | None = None,
    ) -> tuple[User, bool]:
        """Возвращает (user, created)."""
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            # Обновляем данные профиля
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.language_code = language_code
            await self.session.flush()
            return user, False

        user = await self.create(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
            referral_code=referral_code,
        )
        return user, True

    async def increment_requests(self, user_id: int) -> None:
        now = datetime.now(timezone.utc)
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(
                daily_requests_used=User.daily_requests_used + 1,
                total_requests=User.total_requests + 1,
                last_request_at=now,
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def try_consume_daily(self, user_id: int, limit: int) -> tuple[bool, int]:
        """Атомарно списать 1 запрос с учётом дневного лимита.

        Без гонок (TOCTOU): сначала идемпотентный дневной сброс, затем
        условный UPDATE, который инкрементирует ТОЛЬКО если лимит не исчерпан.
        Возвращает (allowed, remaining).
        """
        now = datetime.now(timezone.utc)
        # 1) Идемпотентный дневной сброс (если наступил новый день).
        await self.session.execute(
            update(User)
            .where(
                User.id == user_id,
                (User.last_daily_reset.is_(None))
                | (func.date(User.last_daily_reset) < func.date(now)),
            )
            .values(daily_requests_used=0, last_daily_reset=now)
        )
        # 2) Условный атомарный инкремент.
        stmt = (
            update(User)
            .where(User.id == user_id, User.daily_requests_used < limit)
            .values(
                daily_requests_used=User.daily_requests_used + 1,
                total_requests=User.total_requests + 1,
                last_request_at=now,
            )
            .returning(User.daily_requests_used)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        await self.session.flush()
        if row is None:
            return False, 0
        return True, max(0, limit - row[0])

    async def reset_daily_requests(self, user_id: int | None = None) -> int:
        """Сброс дневных лимитов. Без user_id — для всех."""
        now = datetime.now(timezone.utc)
        stmt = update(User).values(daily_requests_used=0, last_daily_reset=now)
        if user_id:
            stmt = stmt.where(User.id == user_id)
        else:
            stmt = stmt.where(User.daily_requests_used > 0)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount

    async def ban_user(self, user_id: int, reason: str | None = None) -> None:
        await self.update_by_id(user_id, is_banned=True, ban_reason=reason)

    async def unban_user(self, user_id: int) -> None:
        await self.update_by_id(user_id, is_banned=False, ban_reason=None)

    async def set_role(self, user_id: int, role: UserRole) -> None:
        await self.update_by_id(user_id, role=role)

    async def search(
        self,
        query: str,
        offset: int = 0,
        limit: int = 20,
    ) -> Sequence[User]:
        """Поиск по username, first_name или telegram_id."""
        stmt = (
            select(User)
            .where(
                (User.username.ilike(f"%{query}%"))
                | (User.first_name.ilike(f"%{query}%"))
                | (User.telegram_id == int(query) if query.isdigit() else False)
            )
            .offset(offset)
            .limit(limit)
            .order_by(User.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_new_since(self, since: datetime) -> int:
        stmt = select(func.count()).select_from(User).where(User.created_at >= since)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def count_active_since(self, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.last_request_at >= since)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_by_referral_code(self, code: str) -> Optional[User]:
        return await self.get_one(referral_code=code)
