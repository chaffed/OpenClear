# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""audit_log_checkpoint: audit entries archived to signed files (SCALE_PLAN.md S7)

Revision ID: b9d1f3a5c7e8
Revises: a8c0e2f4b6d9
Create Date: 2026-10-09 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b9d1f3a5c7e8'
down_revision: Union[str, None] = 'a8c0e2f4b6d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'audit_log_checkpoint',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('first_sequence', sa.BigInteger(), nullable=False),
        sa.Column('through_sequence', sa.BigInteger(), nullable=False),
        sa.Column('through_entry_hash', sa.String(length=200), nullable=False),
        sa.Column('entry_count', sa.BigInteger(), nullable=False),
        sa.Column('archive_ref', sa.String(length=1000), nullable=False),
        sa.Column('archive_sha256', sa.String(length=64), nullable=False),
        sa.Column('archive_signature', sa.String(length=200), nullable=False),
        sa.Column('oldest_occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('newest_occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_audit_log_checkpoint_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log_checkpoint')),
        sa.UniqueConstraint('tenant_id', 'through_sequence', name='uq_audit_log_checkpoint_tenant_through'),
    )
    op.create_index(op.f('ix_audit_log_checkpoint_tenant_id'), 'audit_log_checkpoint', ['tenant_id'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "audit_log_checkpoint" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "audit_log_checkpoint" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_audit_log_checkpoint ON "audit_log_checkpoint" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_audit_log_checkpoint ON "audit_log_checkpoint"')
    op.drop_index(op.f('ix_audit_log_checkpoint_tenant_id'), table_name='audit_log_checkpoint')
    op.drop_table('audit_log_checkpoint')
