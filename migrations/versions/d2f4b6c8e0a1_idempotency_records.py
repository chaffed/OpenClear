# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""idempotency_record: Idempotency-Key replays for API writes (SDK_PLAN.md P0)

Revision ID: d2f4b6c8e0a1
Revises: c1e3a5b7d9f0
Create Date: 2026-10-09 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2f4b6c8e0a1'
down_revision: Union[str, None] = 'c1e3a5b7d9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'idempotency_record',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('key', sa.String(length=255), nullable=False),
        sa.Column('request_hash', sa.String(length=64), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('content_type', sa.String(length=100), nullable=True),
        sa.Column('response_body', sa.LargeBinary(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_idempotency_record_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_idempotency_record_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_idempotency_record')),
        sa.UniqueConstraint('tenant_id', 'user_id', 'key', name='uq_idempotency_record_caller_key'),
    )
    op.create_index(op.f('ix_idempotency_record_tenant_id'), 'idempotency_record', ['tenant_id'])
    op.create_index('ix_idempotency_record_created_at', 'idempotency_record', ['created_at'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "idempotency_record" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "idempotency_record" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_idempotency_record ON "idempotency_record" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_idempotency_record ON "idempotency_record"')
    op.drop_index('ix_idempotency_record_created_at', table_name='idempotency_record')
    op.drop_index(op.f('ix_idempotency_record_tenant_id'), table_name='idempotency_record')
    op.drop_table('idempotency_record')
