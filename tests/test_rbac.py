"""RBAC: матрица прав и определение Owner (без БД)."""
from metabot.models.user import UserRole
from metabot.security.roles import (
    Permission, has_permission, effective_role, require_permission, PermissionDenied,
)

OWNER = 7221385215


def test_user_baseline():
    assert has_permission(UserRole.USER, Permission.USE_BOT)
    assert not has_permission(UserRole.USER, Permission.BAN_USER)
    assert not has_permission(UserRole.USER, Permission.MANAGE_ROLES)


def test_moderator_can_ban_not_broadcast():
    assert has_permission(UserRole.MODERATOR, Permission.BAN_USER)
    assert not has_permission(UserRole.MODERATOR, Permission.BROADCAST)


def test_admin_broadcast_not_roles():
    assert has_permission(UserRole.ADMIN, Permission.BROADCAST)
    assert has_permission(UserRole.ADMIN, Permission.APPROVE_SUBSCRIPTION)
    assert not has_permission(UserRole.ADMIN, Permission.MANAGE_ROLES)
    assert not has_permission(UserRole.ADMIN, Permission.DESTRUCTIVE_OPS)


def test_owner_has_everything():
    for perm in Permission:
        assert has_permission(UserRole.OWNER, perm)


def test_owner_by_telegram_id_overrides_db_role():
    # роль в БД 'user', но это владелец по telegram_id
    assert effective_role(UserRole.USER, OWNER, OWNER) == UserRole.OWNER
    assert has_permission(UserRole.USER, Permission.MANAGE_ROLES, telegram_id=OWNER, owner_id=OWNER)


def test_inheritance_rank():
    # admin наследует права модератора
    assert has_permission(UserRole.ADMIN, Permission.BAN_USER)


def test_require_permission_raises():
    try:
        require_permission(UserRole.USER, Permission.BAN_USER)
    except PermissionDenied as e:
        assert e.permission == Permission.BAN_USER
    else:
        raise AssertionError("PermissionDenied не поднят")


def test_legacy_roles():
    # legacy premium = как user, superadmin = как admin
    assert not has_permission(UserRole.PREMIUM, Permission.BAN_USER)
    assert has_permission(UserRole.SUPERADMIN, Permission.BROADCAST)
    assert not has_permission(UserRole.SUPERADMIN, Permission.MANAGE_ROLES)
