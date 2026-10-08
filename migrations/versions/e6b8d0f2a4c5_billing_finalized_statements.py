# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""billing: finalized statements and their lines (BILLING_PLAN.md B2)

Revision ID: e6b8d0f2a4c5
Revises: d5a7c9e1f3b4
Create Date: 2026-10-08 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e6b8d0f2a4c5'
down_revision: Union[str, None] = 'd5a7c9e1f3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_TABLES = ('billing_statement', 'billing_statement_line')


def upgrade() -> None:
    op.create_table(
        'billing_statement',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=True),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('superseded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('customer_number', sa.String(length=64), nullable=True),
        sa.Column('customer_name', sa.String(length=255), nullable=False),
        sa.Column('billing_method', sa.Enum('ANALYSIS', 'HARD_CHARGE', 'EXEMPT', name='billing_method', native_enum=False, length=20), nullable=False),
        sa.Column('billing_account_id', sa.Uuid(), nullable=True),
        sa.Column('billing_account_number', sa.String(length=64), nullable=True),
        sa.Column('list_total', sa.Numeric(14, 2), nullable=False),
        sa.Column('discount_total', sa.Numeric(14, 2), nullable=False),
        sa.Column('waived_total', sa.Numeric(14, 2), nullable=False),
        sa.Column('net_total', sa.Numeric(14, 2), nullable=False),
        sa.Column('finalized_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finalized_by_user_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_billing_statement_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_billing_statement_customer_id_customer')),
        sa.ForeignKeyConstraint(['finalized_by_user_id'], ['user.id'], name=op.f('fk_billing_statement_finalized_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_billing_statement')),
    )
    op.create_index(op.f('ix_billing_statement_tenant_id'), 'billing_statement', ['tenant_id'])
    op.create_index(op.f('ix_billing_statement_customer_id'), 'billing_statement', ['customer_id'])
    op.create_index('ix_billing_statement_period', 'billing_statement', ['tenant_id', 'period_start', 'period_end'])

    op.create_table(
        'billing_statement_line',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('statement_id', sa.Uuid(), nullable=False),
        sa.Column('line_number', sa.Integer(), nullable=False),
        sa.Column('fee_item_id', sa.Uuid(), nullable=True),
        sa.Column('fee_code', sa.String(length=40), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=False),
        sa.Column('metric', sa.String(length=60), nullable=False),
        sa.Column('unit', sa.String(length=10), nullable=False),
        sa.Column('afp_code', sa.String(length=16), nullable=True),
        sa.Column('bank_service_code', sa.String(length=32), nullable=True),
        sa.Column('taxable', sa.Boolean(), nullable=False),
        sa.Column('volume', sa.Integer(), nullable=False),
        sa.Column('unit_price', sa.Numeric(12, 4), nullable=False),
        sa.Column('charge_method', sa.String(length=4), nullable=False),
        sa.Column('list_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('discount_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('net_amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('payment_method', sa.String(length=4), nullable=False),
        sa.Column('tiers', sa.JSON(), nullable=True),
        sa.Column('accounts', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_billing_statement_line_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['statement_id'], ['billing_statement.id'], name=op.f('fk_billing_statement_line_statement_id_billing_statement'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_billing_statement_line')),
    )
    op.create_index(op.f('ix_billing_statement_line_tenant_id'), 'billing_statement_line', ['tenant_id'])
    op.create_index(op.f('ix_billing_statement_line_statement_id'), 'billing_statement_line', ['statement_id'])

    if op.get_bind().dialect.name == 'postgresql':
        for table in _TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        for table in _TABLES:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
    op.drop_table('billing_statement_line')
    op.drop_table('billing_statement')
