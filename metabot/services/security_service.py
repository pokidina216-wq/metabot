"""
Сервис безопасности и аудита (Этап 2).

- Запись неизменяемых записей аудита (before/after).
- Запись событий безопасности.
- Помощник защиты Owner: пометить владельца как protected и проверки в коде
  (дублируют триггер БД — defense in depth).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from metabot.models.audit import AuditLog, SecurityEvent
from metabot.models.user import User, UserRole
from metabot.security.roles import is_owner

logger = logging.getLogger(__name__)


class OwnerProtectionError(Exception):
    """Попытка изменить/удалить защищённого владельца."""


class SecurityAuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Аудит ────────────────────────────────────────────────
    async def record(
        self,
        action: str,
        *,
        actor_tg_id: Optional[int] = None,
        actor_user_id: Optional[int] = None,
        actor_role: Optional[str] = None,
        target_user_id: Optional[int] = None,
        target_tg_id: Optional[int] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        before: Optional[dict[str, Any]] = None,
        after: Optional[dict[str, Any]] = None,
        note: Optional[str] = None,
    ) -> AuditLog:
        entry = AuditLog(
            action=action,
            actor_tg_id=actor_tg_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            target_user_id=target_user_id,
            target_tg_id=target_tg_id,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            note=note,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def security_event(
        self,
        event_type: str,
        *,
        severity: str = "info",
        actor_tg_id: Optional[int] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> SecurityEvent:
        ev = SecurityEvent(
            event_type=event_type,
            severity=severity,
            actor_tg_id=actor_tg_id,
            detail=detail,
        )
        self.session.add(ev)
        await self.session.flush()
        logger.info(
            "security_event", extra={"event_type": event_type, "severity": severity,
                                     "actor_tg_id": actor_tg_id}
        )
        return ev

    # ── Защита Owner ─────────────────────────────────────────
    async def ensure_owner_protected(self, owner_id: int) -> Optional[User]:
        """Гарантирует, что запись владельца помечена protected и имеет роль OWNER.

        Вызывается при старте/первом обращении владельца. Идемпотентно.
        """
        if not owner_id:
            return None
        user = (
            await self.session.execute(
                select(User).where(User.telegram_id == owner_id)
            )
        ).scalar_one_or_none()
        if user is None:
            return None
        changed = False
        if not user.is_protected:
            # Снимаем защиту перед апдейтом нельзя — но включать защиту можно.
            user.is_protected = True
            changed = True
        if user.role != UserRole.OWNER:
            user.role = UserRole.OWNER
            changed = True
        if user.is_banned:
            user.is_banned = False
            user.ban_reason = None
            changed = True
        if changed:
            await self.session.flush()
            await self.security_event(
                "owner_protection_applied", severity="info", actor_tg_id=owner_id,
                detail={"user_id": user.id},
            )
        return user

    async def assert_not_owner_target(
        self, target: User, owner_id: int, *, op: str, actor_tg_id: Optional[int] = None
    ) -> None:
        """Код-уровневая защита: запрет деструктивных операций над владельцем.

        Дублирует триггер БД (defense in depth). Бросает OwnerProtectionError.
        """
        if target.is_protected or is_owner(target.telegram_id, owner_id):
            await self.security_event(
                "owner_protection_block", severity="critical",
                actor_tg_id=actor_tg_id,
                detail={"op": op, "target_tg_id": target.telegram_id},
            )
            raise OwnerProtectionError(
                f"Операция '{op}' над владельцем запрещена."
            )
