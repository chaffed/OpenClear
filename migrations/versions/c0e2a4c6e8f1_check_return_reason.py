# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Check return reasons, seeded for every bank (UX_PLAN.md U4; MASTER_PLAN.md M3)

Revision ID: c0e2a4c6e8f1
Revises: b9d1f3a5c7e0
Create Date: 2026-10-11 10:00:00.000000
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c0e2a4c6e8f1'
down_revision: Union[str, None] = 'b9d1f3a5c7e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"

# A copy of services/check_return_reason_service.DEFAULT_CHECK_RETURN_REASONS as it was when
# this migration was written (a migration mustn't change if that list does).
_DEFAULTS = [
    ("Altered or fictitious item", "N"),
    ("Not authorized", "Q"),
    ("Stop payment", "C"),
    ("Duplicate presentment", "Y"),
    ("Refer to maker", "S"),
    ("Stale dated", "G"),
    ("Signature irregular", "L"),
]


def upgrade() -> None:
    table = op.create_table(
        'check_return_reason',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('reason_text', sa.String(length=255), nullable=False),
        sa.Column('return_code', sa.String(length=4), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_check_return_reason_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_check_return_reason')),
    )
    op.create_index(op.f('ix_check_return_reason_tenant_id'), 'check_return_reason', ['tenant_id'])
    tenants = op.get_bind().execute(sa.text('SELECT id FROM tenant')).scalars().all()
    if tenants:
        op.bulk_insert(table, [
            {'id': uuid.uuid4(), 'tenant_id': tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id)),
             'reason_text': text, 'return_code': code, 'is_active': True}
            for tenant_id in tenants for text, code in _DEFAULTS
        ])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "check_return_reason" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "check_return_reason" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_check_return_reason ON "check_return_reason" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_check_return_reason ON "check_return_reason"')
    op.drop_table('check_return_reason')
