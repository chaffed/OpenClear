# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Security events (SIEM_PLAN.md S2; MASTER_PLAN.md M1)

No row-level security, like job and notification: some events belong to no bank (a failed
sign-in naming a bank that doesn't exist), and delivery reads every bank's. Every read
filters by bank in services/security_events.py.

Revision ID: b9d1f3a5c7e0
Revises: a8c0e2a4c6e9
Create Date: 2026-10-10 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b9d1f3a5c7e0'
down_revision: Union[str, None] = 'a8c0e2a4c6e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'security_event',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('outcome', sa.String(length=10), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('reason', sa.String(length=60), nullable=True),
        sa.Column('actor_type', sa.String(length=20), nullable=True),
        sa.Column('actor_id', sa.String(length=64), nullable=True),
        sa.Column('actor_label', sa.String(length=320), nullable=True),
        sa.Column('target_type', sa.String(length=40), nullable=True),
        sa.Column('target_id', sa.String(length=64), nullable=True),
        sa.Column('target_label', sa.String(length=320), nullable=True),
        sa.Column('source_ip', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=300), nullable=True),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('edge_id', sa.String(length=100), nullable=True),
        sa.Column('channel', sa.String(length=10), nullable=True),
        sa.Column('audit_sequence', sa.BigInteger(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_security_event_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_security_event')),
    )
    op.create_index('ix_security_event_tenant_id_id', 'security_event', ['tenant_id', 'id'])
    op.create_index('ix_security_event_occurred_at', 'security_event', ['occurred_at'])


def downgrade() -> None:
    op.drop_table('security_event')
