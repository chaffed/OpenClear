# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Teller verification: a check verified before cash is paid at a branch, and recorded cashed
(TELLER_PLAN.md TL-1, TL-2)

Adds teller_verification, with row-level security.

Revision ID: 4c47d3f3a975
Revises: 7f7bc3cb2b34
Create Date: 2026-10-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4c47d3f3a975'
down_revision: Union[str, None] = '7f7bc3cb2b34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'teller_verification',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('account_id', sa.Uuid(), nullable=True),
        sa.Column('customer_id', sa.Uuid(), nullable=True),
        sa.Column('routing_number', sa.String(length=9), nullable=True),
        sa.Column('account_number', sa.String(length=64), nullable=False),
        sa.Column('check_number', sa.String(length=32), nullable=False),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('payee_name', sa.String(length=255), nullable=True),
        sa.Column('result', sa.String(length=30), nullable=False),
        sa.Column('problems', sa.JSON(), nullable=False),
        sa.Column('issued_item_id', sa.Uuid(), nullable=True),
        sa.Column('teller_id', sa.String(length=64), nullable=True),
        sa.Column('branch', sa.String(length=64), nullable=True),
        sa.Column('api_client_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('cashed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paid_item_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_teller_verification_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['account_id'], ['account.id'], name=op.f('fk_teller_verification_account_id_account')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_teller_verification_customer_id_customer')),
        sa.ForeignKeyConstraint(['issued_item_id'], ['issued_item.id'], name=op.f('fk_teller_verification_issued_item_id_issued_item')),
        sa.ForeignKeyConstraint(['paid_item_id'], ['paid_item.id'], name=op.f('fk_teller_verification_paid_item_id_paid_item')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_teller_verification')),
    )
    for column in ('tenant_id', 'account_id', 'customer_id', 'created_at'):
        op.create_index(op.f(f'ix_teller_verification_{column}'), 'teller_verification', [column], unique=False)
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "teller_verification" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "teller_verification" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_teller_verification ON "teller_verification" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_teller_verification ON "teller_verification"')
    for column in ('tenant_id', 'account_id', 'customer_id', 'created_at'):
        op.drop_index(op.f(f'ix_teller_verification_{column}'), table_name='teller_verification')
    op.drop_table('teller_verification')
