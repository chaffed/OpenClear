# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""tenant_extension: plugin import formats and decision layouts a bank has turned on (PLUGIN_PLAN.md PL2)

Revision ID: a6c8e0b2d4f5
Revises: f4b6d8e0a2c3
Create Date: 2026-10-08 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a6c8e0b2d4f5'
down_revision: Union[str, None] = 'f4b6d8e0a2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'tenant_extension',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('point', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('enabled_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('enabled_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_tenant_extension_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['enabled_by_user_id'], ['user.id'], name=op.f('fk_tenant_extension_enabled_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tenant_extension')),
        sa.UniqueConstraint('tenant_id', 'point', 'name', name='uq_tenant_extension'),
    )
    op.create_index(op.f('ix_tenant_extension_tenant_id'), 'tenant_extension', ['tenant_id'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "tenant_extension" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "tenant_extension" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_tenant_extension ON "tenant_extension" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_tenant_extension ON "tenant_extension"')
    op.drop_table('tenant_extension')
