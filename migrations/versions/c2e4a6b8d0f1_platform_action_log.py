# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The service provider's action log (DUAL_CONTROL_PLAN.md DC4)

Revision ID: c2e4a6b8d0f1
Revises: b1d3f5a7c9e2
Create Date: 2026-10-18 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2e4a6b8d0f1'
down_revision: Union[str, None] = 'b1d3f5a7c9e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NO_BANK = "NULLIF(current_setting('app.current_tenant_id', true), '') IS NULL"


def upgrade() -> None:
    op.create_table(
        'platform_action_log',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('sequence', sa.BigInteger(), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('actor_user_id', sa.Uuid(), nullable=True),
        sa.Column('actor_key_id', sa.Uuid(), nullable=True),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('summary', sa.String(length=500), nullable=False),
        sa.Column('resource_type', sa.String(length=50), nullable=False),
        sa.Column('resource_id', sa.Uuid(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('prev_entry_hash', sa.String(length=200), nullable=True),
        sa.Column('entry_hash', sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(['actor_user_id'], ['user.id'], name=op.f('fk_platform_action_log_actor_user_id_user')),
        sa.ForeignKeyConstraint(['actor_key_id'], ['platform_api_key.id'], name=op.f('fk_platform_action_log_actor_key_id_platform_api_key')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_platform_action_log')),
        sa.UniqueConstraint('sequence', name=op.f('uq_platform_action_log_sequence')),
    )
    if op.get_bind().dialect.name == "postgresql":
        # The installation's own: read and written only from a session bound to no bank.
        op.execute('ALTER TABLE "platform_action_log" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "platform_action_log" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY platform_only_platform_action_log ON "platform_action_log" USING ({_NO_BANK}) WITH CHECK ({_NO_BANK})')


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS platform_only_platform_action_log ON "platform_action_log"')
    op.drop_table('platform_action_log')
