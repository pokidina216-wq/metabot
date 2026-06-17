"""stage2 security: owner role, owner protection, audit log

Revision ID: 9b2c4e1f0a77
Revises: 802d3d950060
Create Date: 2026-06-17 01:30:00.000000

Этап 2 (безопасность):
- добавляем значение 'owner' в enum user_role;
- колонка users.is_protected + триггер защиты Owner (запрет бана/понижения/
  снятия защиты/удаления защищённой записи);
- таблицы audit_log и security_events;
- триггеры append-only (запрет UPDATE/DELETE) на журналы.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '9b2c4e1f0a77'
down_revision: Union[str, None] = '802d3d950060'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Новое значение роли. ADD VALUE IF NOT EXISTS идемпотентно (PG 12+).
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'owner'")

    # 2) Флаг защиты владельца
    op.add_column(
        "users",
        sa.Column("is_protected", sa.Boolean(), nullable=False, server_default="false"),
    )

    # 3) Журнал аудита (append-only)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_tg_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_role", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        sa.Column("target_tg_id", sa.BigInteger(), nullable=True),
        sa.Column("target_type", sa.String(length=48), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_actor_user_id", "audit_log", ["actor_user_id"])
    op.create_index("ix_audit_log_actor_tg_id", "audit_log", ["actor_tg_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_action_created", "audit_log", ["action", "created_at"])

    # 4) Журнал событий безопасности (append-only)
    op.create_table(
        "security_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("actor_tg_id", sa.BigInteger(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_security_events_event_type", "security_events", ["event_type"])
    op.create_index("ix_security_events_actor_tg_id", "security_events", ["actor_tg_id"])
    op.create_index("ix_security_events_created_at", "security_events", ["created_at"])
    op.create_index("ix_security_events_type_created", "security_events",
                    ["event_type", "created_at"])

    # 5) Триггер защиты Owner на users
    op.execute(
        """
        CREATE OR REPLACE FUNCTION vexis_protect_owner()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.is_protected THEN
                    RAISE EXCEPTION 'owner_protected: cannot delete protected owner (tg_id=%)', OLD.telegram_id;
                END IF;
                RETURN OLD;
            END IF;
            -- UPDATE
            IF OLD.is_protected THEN
                IF NEW.is_banned IS TRUE THEN
                    RAISE EXCEPTION 'owner_protected: cannot ban protected owner (tg_id=%)', OLD.telegram_id;
                END IF;
                IF NEW.role IS DISTINCT FROM OLD.role THEN
                    RAISE EXCEPTION 'owner_protected: cannot change role of protected owner (tg_id=%)', OLD.telegram_id;
                END IF;
                IF NEW.is_protected IS DISTINCT FROM TRUE THEN
                    RAISE EXCEPTION 'owner_protected: cannot remove protection (tg_id=%)', OLD.telegram_id;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_protect_owner ON users")
    op.execute(
        """
        CREATE TRIGGER trg_protect_owner
        BEFORE UPDATE OR DELETE ON users
        FOR EACH ROW EXECUTE FUNCTION vexis_protect_owner();
        """
    )

    # 6) Append-only триггер для журналов
    op.execute(
        """
        CREATE OR REPLACE FUNCTION vexis_append_only()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'append_only: % on % is not allowed', TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for tbl in ("audit_log", "security_events"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_append_only_{tbl} ON {tbl}")
        op.execute(
            f"""
            CREATE TRIGGER trg_append_only_{tbl}
            BEFORE UPDATE OR DELETE ON {tbl}
            FOR EACH ROW EXECUTE FUNCTION vexis_append_only();
            """
        )


def downgrade() -> None:
    for tbl in ("audit_log", "security_events"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_append_only_{tbl} ON {tbl}")
    op.execute("DROP TRIGGER IF EXISTS trg_protect_owner ON users")
    op.execute("DROP FUNCTION IF EXISTS vexis_append_only()")
    op.execute("DROP FUNCTION IF EXISTS vexis_protect_owner()")

    op.drop_table("security_events")
    op.drop_table("audit_log")
    op.drop_column("users", "is_protected")
    # Значение 'owner' в enum user_role оставляем: PostgreSQL не поддерживает
    # удаление значений enum без пересоздания типа.
