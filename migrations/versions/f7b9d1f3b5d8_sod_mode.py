# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Segregation of duties: tenant.sod_mode and change_request (COMPLIANCE_PLAN.md C1)

Revision ID: f7b9d1f3b5d8
Revises: e6a8c0e2a4c7
Create Date: 2026-10-10 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7b9d1f3b5d8'
down_revision: Union[str, None] = 'e6a8c0e2a4c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('sod_mode', sa.String(length=10), nullable=False, server_default='warn'))
    op.create_table(
        'change_request',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=60), nullable=False),
        sa.Column('summary', sa.String(length=500), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('requested_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('decided_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_change_request_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['requested_by_user_id'], ['user.id'], name=op.f('fk_change_request_requested_by_user_id_user')),
        sa.ForeignKeyConstraint(['decided_by_user_id'], ['user.id'], name=op.f('fk_change_request_decided_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_change_request')),
    )
    op.create_index(op.f('ix_change_request_tenant_id'), 'change_request', ['tenant_id'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "change_request" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "change_request" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_change_request ON "change_request" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_change_request ON "change_request"')
    op.drop_table('change_request')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('sod_mode')
