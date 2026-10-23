# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Offboarding a bank or a customer (plans/OFFBOARDING_PLAN.md, plans/CUSTOMER_OFFBOARDING_PLAN.md)

tenant.offboarding_started_at, tenant.data_deleted_at and tenant.legal_hold_reason; bank_deletion, the service provider's
record of each deletion with its signed certificate. bank_deletion has no tenant_id, so no
row-level security: it outlives the bank's own rows. customer.service_ends_at, service_ended_at
and legal_hold_reason; customer_deletion, the bank's record of each customer's deletion, with
row-level security.

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

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


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
    with op.batch_alter_table('customer') as batch_op:
        batch_op.add_column(sa.Column('service_ends_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('service_ended_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('legal_hold_reason', sa.String(length=500), nullable=True))
    op.create_table(
        'customer_deletion',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=False),
        sa.Column('customer_number', sa.String(length=64), nullable=False),
        sa.Column('customer_name', sa.String(length=255), nullable=False),
        sa.Column('service_ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('certificate', sa.JSON(), nullable=False),
        sa.Column('certificate_sha256', sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_customer_deletion_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_customer_deletion')),
    )
    op.create_index(op.f('ix_customer_deletion_tenant_id'), 'customer_deletion', ['tenant_id'], unique=False)
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "customer_deletion" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "customer_deletion" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_customer_deletion ON "customer_deletion" USING ({_TENANT})')


def downgrade() -> None:
    op.drop_index(op.f('ix_customer_deletion_tenant_id'), table_name='customer_deletion')
    op.drop_table('customer_deletion')
    with op.batch_alter_table('customer') as batch_op:
        batch_op.drop_column('legal_hold_reason')
        batch_op.drop_column('service_ended_at')
        batch_op.drop_column('service_ends_at')
    op.drop_index(op.f('ix_bank_deletion_bank_id'), table_name='bank_deletion')
    op.drop_table('bank_deletion')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('legal_hold_reason')
        batch_op.drop_column('data_deleted_at')
        batch_op.drop_column('offboarding_started_at')
