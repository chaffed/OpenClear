# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Integration checklist: conformance_result (SDK_PLAN.md P4)

Revision ID: d5f7b9d1f3b6
Revises: c4e6a8c0e2a5
Create Date: 2026-10-09 05:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5f7b9d1f3b6'
down_revision: Union[str, None] = 'c4e6a8c0e2a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'conformance_result',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('check', sa.String(length=60), nullable=False),
        sa.Column('passed', sa.Boolean(), nullable=False),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_conformance_result_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_conformance_result')),
        sa.UniqueConstraint('tenant_id', 'check', name='uq_conformance_result_check'),
    )
    op.create_index(op.f('ix_conformance_result_tenant_id'), 'conformance_result', ['tenant_id'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "conformance_result" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "conformance_result" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_conformance_result ON "conformance_result" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_conformance_result ON "conformance_result"')
    op.drop_table('conformance_result')
