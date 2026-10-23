# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Offboarding a bank (plans/OFFBOARDING_PLAN.md)

tenant.offboarding_started_at, tenant.data_deleted_at and tenant.legal_hold_reason; bank_deletion, the service provider's
record of each deletion with its signed certificate. bank_deletion has no tenant_id, so no
row-level security: it outlives the bank's own rows.

Revision ID: b3f1c7a9e2d5
Revises: d867a5f28e73
Create Date: 2026-10-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f1c7a9e2d5'
down_revision: Union[str, None] = 'd867a5f28e73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('offboarding_started_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('data_deleted_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('legal_hold_reason', sa.String(length=500), nullable=True))
    op.create_table(
        'bank_deletion',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('bank_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('requested_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('approved_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('runs_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('override_reason', sa.String(length=500), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('certificate', sa.JSON(), nullable=True),
        sa.Column('certificate_sha256', sa.String(length=64), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['bank_id'], ['tenant.id'], name=op.f('fk_bank_deletion_bank_id_tenant')),
        sa.ForeignKeyConstraint(['requested_by_user_id'], ['user.id'], name=op.f('fk_bank_deletion_requested_by_user_id_user')),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['user.id'], name=op.f('fk_bank_deletion_approved_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_deletion')),
    )
    op.create_index(op.f('ix_bank_deletion_bank_id'), 'bank_deletion', ['bank_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_bank_deletion_bank_id'), table_name='bank_deletion')
    op.drop_table('bank_deletion')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('legal_hold_reason')
        batch_op.drop_column('data_deleted_at')
        batch_op.drop_column('offboarding_started_at')
