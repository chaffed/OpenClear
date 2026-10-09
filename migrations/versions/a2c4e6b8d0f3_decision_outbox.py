# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Decision outbox: decision_outbox and decision_batch (SDK_PLAN.md P3)

Revision ID: a2c4e6b8d0f3
Revises: f1b3d5f7a9c2
Create Date: 2026-10-09 03:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2c4e6b8d0f3'
down_revision: Union[str, None] = 'f1b3d5f7a9c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_TABLES = ('decision_outbox', 'decision_batch')


def upgrade() -> None:
    op.create_table(
        'decision_outbox',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('layout', sa.String(length=100), nullable=False, server_default='openclear_csv'),
        sa.Column('interval_minutes', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('layout_settings', sa.JSON(), nullable=False),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_decision_outbox_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_decision_outbox')),
        sa.UniqueConstraint('tenant_id', name=op.f('uq_decision_outbox_tenant_id')),
    )
    op.create_table(
        'decision_batch',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('layout', sa.String(length=100), nullable=False),
        sa.Column('from_sequence', sa.BigInteger(), nullable=False),
        sa.Column('to_sequence', sa.BigInteger(), nullable=False),
        sa.Column('decision_count', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=200), nullable=False),
        sa.Column('media_type', sa.String(length=100), nullable=False),
        sa.Column('storage_ref', sa.String(length=1000), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_by_user_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_decision_batch_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['acknowledged_by_user_id'], ['user.id'], name=op.f('fk_decision_batch_acknowledged_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_decision_batch')),
    )
    op.create_index(op.f('ix_decision_batch_tenant_id'), 'decision_batch', ['tenant_id'])
    if op.get_bind().dialect.name == 'postgresql':
        for table in _TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        for table in _TABLES:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
    op.drop_table('decision_batch')
    op.drop_table('decision_outbox')
