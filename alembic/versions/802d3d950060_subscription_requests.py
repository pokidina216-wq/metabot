"""subscription_requests

Revision ID: 802d3d950060
Revises: 1a153d85b1dc
Create Date: 2026-06-17 00:57:51.830619
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '802d3d950060'
down_revision: Union[str, None] = '1a153d85b1dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ВАЖНО: create_type работает только у postgresql.ENUM, а у sa.Enum
# игнорируется — поэтому op.create_table с sa.Enum дважды эмитит CREATE TYPE.
# Решение: тип создаём идемпотентным DO-блоком, а в таблице используем
# postgresql.ENUM(create_type=False), чтобы create_table тип НЕ трогал.
_ENUM_VALUES = ('pending', 'approved', 'rejected', 'cancelled')
status_enum = postgresql.ENUM(
    *_ENUM_VALUES, name='subscription_request_status', create_type=False,
)


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = "
        "'subscription_request_status') THEN "
        "CREATE TYPE subscription_request_status AS ENUM "
        "('pending', 'approved', 'rejected', 'cancelled'); "
        "END IF; END $$;"
    )

    op.create_table('subscription_requests',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('plan_id', sa.Integer(), nullable=False),
    sa.Column('status', status_enum, server_default='pending', nullable=False),
    sa.Column('decided_by', sa.BigInteger(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('subscription_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sub_requests_status', 'subscription_requests', ['status'], unique=False)
    op.create_index('ix_sub_requests_user_status', 'subscription_requests', ['user_id', 'status'], unique=False)
    op.create_index(op.f('ix_subscription_requests_plan_id'), 'subscription_requests', ['plan_id'], unique=False)
    op.create_index(op.f('ix_subscription_requests_user_id'), 'subscription_requests', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_subscription_requests_user_id'), table_name='subscription_requests')
    op.drop_index(op.f('ix_subscription_requests_plan_id'), table_name='subscription_requests')
    op.drop_index('ix_sub_requests_user_status', table_name='subscription_requests')
    op.drop_index('ix_sub_requests_status', table_name='subscription_requests')
    op.drop_table('subscription_requests')
    op.execute("DROP TYPE IF EXISTS subscription_request_status")
