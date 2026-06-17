"""Защита Owner: код-уровень + триггеры БД, append-only аудит."""
import pytest
from sqlalchemy import select, text

from metabot.models.user import User, UserRole
from metabot.services.security_service import SecurityAuditService, OwnerProtectionError

OWNER = 7221385215


@pytest.mark.asyncio
async def test_ensure_owner_protected(session_factory):
    async with session_factory() as s:
        s.add(User(telegram_id=OWNER, username="owner", role=UserRole.USER))
        await s.commit()
    async with session_factory() as s:
        u = await SecurityAuditService(s).ensure_owner_protected(OWNER)
        await s.commit()
        assert u.is_protected and u.role == UserRole.OWNER and not u.is_banned


@pytest.mark.asyncio
async def test_trigger_blocks_ban(session_factory):
    async with session_factory() as s:
        owner = (await s.execute(select(User).where(User.telegram_id == OWNER))).scalar_one()
        owner.is_banned = True
        with pytest.raises(Exception) as ei:
            await s.commit()
        assert "owner_protected" in str(ei.value)


@pytest.mark.asyncio
async def test_trigger_blocks_role_change(session_factory):
    async with session_factory() as s:
        owner = (await s.execute(select(User).where(User.telegram_id == OWNER))).scalar_one()
        owner.role = UserRole.USER
        with pytest.raises(Exception) as ei:
            await s.commit()
        assert "owner_protected" in str(ei.value)


@pytest.mark.asyncio
async def test_trigger_blocks_delete(session_factory):
    async with session_factory() as s:
        with pytest.raises(Exception) as ei:
            await s.execute(text("DELETE FROM users WHERE telegram_id=:t"), {"t": OWNER})
            await s.commit()
        assert "owner_protected" in str(ei.value)


@pytest.mark.asyncio
async def test_normal_user_bannable(session_factory):
    async with session_factory() as s:
        s.add(User(telegram_id=222, username="bob", role=UserRole.USER))
        await s.commit()
    async with session_factory() as s:
        bob = (await s.execute(select(User).where(User.telegram_id == 222))).scalar_one()
        bob.is_banned = True
        await s.commit()
        assert bob.is_banned


@pytest.mark.asyncio
async def test_code_guard(session_factory):
    async with session_factory() as s:
        owner = (await s.execute(select(User).where(User.telegram_id == OWNER))).scalar_one()
        svc = SecurityAuditService(s)
        with pytest.raises(OwnerProtectionError):
            await svc.assert_not_owner_target(owner, OWNER, op="ban", actor_tg_id=999)
        await s.commit()


@pytest.mark.asyncio
async def test_audit_append_only(session_factory):
    async with session_factory() as s:
        entry = await SecurityAuditService(s).record("x", actor_tg_id=OWNER, note="n")
        await s.commit()
        eid = entry.id
    async with session_factory() as s:
        with pytest.raises(Exception) as ei:
            await s.execute(text("UPDATE audit_log SET note='y' WHERE id=:i"), {"i": eid})
            await s.commit()
        assert "append_only" in str(ei.value)
