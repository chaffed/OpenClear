# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""webhooks: webhook_endpoint and webhook_delivery (SDK_PLAN.md P0)

Revision ID: f4b6d8e0a2c3
Revises: e3a5c7d9f1b2
Create Date: 2026-10-09 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4b6d8e0a2c3'
down_revision: Union[str, None] = 'e3a5c7d9f1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_TABLES = ('webhook_endpoint', 'webhook_delivery')


def upgrade() -> None:
    op.create_table(
        'webhook_endpoint',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('url', sa.String(length=1000), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=True),
        sa.Column('event_types', sa.JSON(), nullable=False),
        sa.Column('secret_encrypted', sa.String(length=500), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_webhook_endpoint_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['user.id'], name=op.f('fk_webhook_endpoint_created_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_webhook_endpoint')),
    )
    op.create_index(op.f('ix_webhook_endpoint_tenant_id'), 'webhook_endpoint', ['tenant_id'])
    op.create_table(
        'webhook_delivery',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('endpoint_id', sa.Uuid(), nullable=False),
        sa.Column('message_id', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_status_code', sa.Integer(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_webhook_delivery_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['endpoint_id'], ['webhook_endpoint.id'], name=op.f('fk_webhook_delivery_endpoint_id_webhook_endpoint')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_webhook_delivery')),
    )
    op.create_index(op.f('ix_webhook_delivery_tenant_id'), 'webhook_delivery', ['tenant_id'])
    op.create_index('ix_webhook_delivery_endpoint_created', 'webhook_delivery', ['endpoint_id', 'created_at'])
    if op.get_bind().dialect.name == 'postgresql':
        for table in _TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        for table in _TABLES:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
    op.drop_table('webhook_delivery')
    op.drop_table('webhook_endpoint')
