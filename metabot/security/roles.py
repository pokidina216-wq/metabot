"""
RBAC: роли, права и единая точка проверки доступа (PolicyGuard).

Принципы:
- Владелец один: пользователь с telegram_id == settings.owner_id ВСЕГДА
  считается OWNER, независимо от значения в БД. Это нельзя обойти, испортив
  поле role в базе.
- Права назначаются ролям через матрицу ROLE_PERMISSIONS. Роль наследует все
  права ролей ниже по рангу.
- Любая критическая операция проверяется через has_permission / require_permission.
"""
from __future__ import annotations

import enum
from typing import Iterable, Optional, Set

from metabot.models.user import UserRole


class Permission(str, enum.Enum):
    """Атомарные права в системе."""
    # Базовое использование бота
    USE_BOT = "use_bot"
    # Просмотр пользователей/статистики
    VIEW_USERS = "view_users"
    VIEW_STATS = "view_stats"
    VIEW_AUDIT = "view_audit"
    # Модерация
    BAN_USER = "ban_user"
    UNBAN_USER = "unban_user"
    # Подписки (ручное решение Owner)
    APPROVE_SUBSCRIPTION = "approve_subscription"
    # Управление ролями
    MANAGE_ROLES = "manage_roles"
    # Рассылки
    BROADCAST = "broadcast"
    # Управление каталогом OSINT/источниками
    MANAGE_SOURCES = "manage_sources"
    # Опасные/системные операции (только Owner)
    MANAGE_SETTINGS = "manage_settings"
    DESTRUCTIVE_OPS = "destructive_ops"


# Ранг роли — чем выше, тем больше прав. Используется для сравнения и наследования.
ROLE_RANK: dict[UserRole, int] = {
    UserRole.USER: 0,
    UserRole.PREMIUM: 0,        # premium = подписка, прав не добавляет
    UserRole.MODERATOR: 10,
    UserRole.ADMIN: 20,
    UserRole.SUPERADMIN: 30,    # legacy ≈ admin+, маппится близко к owner
    UserRole.OWNER: 100,
}

# Прямые права роли (без наследования). Итоговые права = объединение прав всех
# ролей с рангом <= рангу данной роли (см. _resolve_permissions).
_DIRECT: dict[UserRole, Set[Permission]] = {
    UserRole.USER: {Permission.USE_BOT},
    UserRole.MODERATOR: {
        Permission.VIEW_USERS,
        Permission.VIEW_STATS,
        Permission.BAN_USER,
        Permission.UNBAN_USER,
    },
    UserRole.ADMIN: {
        Permission.VIEW_AUDIT,
        Permission.BROADCAST,
        Permission.MANAGE_SOURCES,
        Permission.APPROVE_SUBSCRIPTION,
    },
    UserRole.OWNER: {
        Permission.MANAGE_ROLES,
        Permission.MANAGE_SETTINGS,
        Permission.DESTRUCTIVE_OPS,
    },
}


def _resolve_permissions(role: UserRole) -> Set[Permission]:
    rank = ROLE_RANK.get(role, 0)
    perms: Set[Permission] = set()
    for r, direct in _DIRECT.items():
        if ROLE_RANK.get(r, 0) <= rank:
            perms |= direct
    return perms


# Готовая матрица «роль → полный набор прав» (с наследованием).
ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    role: frozenset(_resolve_permissions(role)) for role in UserRole
}
# legacy SUPERADMIN: дать тот же набор, что и ADMIN, но без owner-прав
ROLE_PERMISSIONS[UserRole.SUPERADMIN] = ROLE_PERMISSIONS[UserRole.ADMIN]
# legacy PREMIUM: как обычный пользователь
ROLE_PERMISSIONS[UserRole.PREMIUM] = ROLE_PERMISSIONS[UserRole.USER]


class PermissionDenied(Exception):
    """Поднимается, когда у субъекта нет требуемого права."""

    def __init__(self, permission: Permission, role: UserRole) -> None:
        self.permission = permission
        self.role = role
        super().__init__(f"Permission {permission.value} denied for role {role.value}")


def is_owner(telegram_id: Optional[int], owner_id: int) -> bool:
    """True, если данный Telegram ID — владелец системы."""
    return bool(owner_id) and telegram_id == owner_id


def effective_role(db_role: UserRole, telegram_id: Optional[int], owner_id: int) -> UserRole:
    """Реальная роль с учётом владельца.

    Owner определяется по telegram_id, а не по полю в БД — поэтому испорченное
    поле role не может понизить владельца.
    """
    if is_owner(telegram_id, owner_id):
        return UserRole.OWNER
    return db_role or UserRole.USER


def has_permission(
    role: UserRole,
    permission: Permission,
    *,
    telegram_id: Optional[int] = None,
    owner_id: int = 0,
) -> bool:
    """Проверка права. Owner имеет все права безусловно."""
    eff = effective_role(role, telegram_id, owner_id)
    if eff == UserRole.OWNER:
        return True
    return permission in ROLE_PERMISSIONS.get(eff, frozenset())


def require_permission(
    role: UserRole,
    permission: Permission,
    *,
    telegram_id: Optional[int] = None,
    owner_id: int = 0,
) -> None:
    """Бросает PermissionDenied, если права нет."""
    if not has_permission(role, permission, telegram_id=telegram_id, owner_id=owner_id):
        eff = effective_role(role, telegram_id, owner_id)
        raise PermissionDenied(permission, eff)


def permissions_for(role: UserRole) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())
