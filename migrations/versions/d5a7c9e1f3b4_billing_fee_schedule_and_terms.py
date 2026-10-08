# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""billing: fee schedule, customer billing terms and fee overrides, bank billing settings

Revision ID: d5a7c9e1f3b4
Revises: c4f6a8b0d2e5
Create Date: 2026-10-08 12:00:00.000000

BILLING_PLAN.md phase B1. Existing customers have no billing terms row, so they're billed by
the bank's default (analysis); the bank's own accounts default to exempt. Existing "Admin"
groups get the two new billing permissions.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5a7c9e1f3b4'
down_revision: Union[str, None] = 'c4f6a8b0d2e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_METHOD = sa.Enum('ANALYSIS', 'HARD_CHARGE', 'EXEMPT', name='billing_method', native_enum=False, length=20)
_RLS_TABLES = ('billing_fee_item', 'customer_billing_terms', 'customer_fee_override')
_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'billing_fee_item',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=False),
        sa.Column('metric', sa.String(length=60), nullable=False),
        sa.Column('unit_price', sa.Numeric(12, 4), nullable=False),
        sa.Column('tiers', sa.JSON(), nullable=True),
        sa.Column('minimum_charge', sa.Numeric(12, 2), nullable=True),
        sa.Column('afp_code', sa.String(length=16), nullable=True),
        sa.Column('bank_service_code', sa.String(length=32), nullable=True),
        sa.Column('taxable', sa.Boolean(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('effective_from', sa.Date(), nullable=True),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_billing_fee_item_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_billing_fee_item')),
        sa.UniqueConstraint('tenant_id', 'code', name='uq_billing_fee_item_tenant_code'),
    )
    op.create_index(op.f('ix_billing_fee_item_tenant_id'), 'billing_fee_item', ['tenant_id'])

    op.create_table(
        'customer_billing_terms',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=False),
        sa.Column('billing_method', _METHOD, nullable=False),
        sa.Column('billing_account_id', sa.Uuid(), nullable=True),
        sa.Column('discount_percent', sa.Numeric(5, 2), nullable=False),
        sa.Column('waive_all', sa.Boolean(), nullable=False),
        sa.Column('notes', sa.String(length=500), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_customer_billing_terms_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_customer_billing_terms_customer_id_customer')),
        sa.ForeignKeyConstraint(['billing_account_id'], ['account.id'], name=op.f('fk_customer_billing_terms_billing_account_id_account')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_customer_billing_terms')),
        sa.UniqueConstraint('customer_id', name='uq_customer_billing_terms_customer'),
    )
    op.create_index(op.f('ix_customer_billing_terms_tenant_id'), 'customer_billing_terms', ['tenant_id'])
    op.create_index(op.f('ix_customer_billing_terms_customer_id'), 'customer_billing_terms', ['customer_id'])

    op.create_table(
        'customer_fee_override',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=False),
        sa.Column('fee_item_id', sa.Uuid(), nullable=False),
        sa.Column('unit_price', sa.Numeric(12, 4), nullable=True),
        sa.Column('waived', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_customer_fee_override_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_customer_fee_override_customer_id_customer')),
        sa.ForeignKeyConstraint(['fee_item_id'], ['billing_fee_item.id'], name=op.f('fk_customer_fee_override_fee_item_id_billing_fee_item'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_customer_fee_override')),
        sa.UniqueConstraint('customer_id', 'fee_item_id', name='uq_customer_fee_override'),
    )
    op.create_index(op.f('ix_customer_fee_override_tenant_id'), 'customer_fee_override', ['tenant_id'])
    op.create_index(op.f('ix_customer_fee_override_customer_id'), 'customer_fee_override', ['customer_id'])

    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column(
            'customer_default_billing_method',
            sa.Enum('ANALYSIS', 'HARD_CHARGE', 'EXEMPT', name='customer_default_billing_method', native_enum=False, length=20),
            nullable=False, server_default='ANALYSIS',
        ))
        batch_op.add_column(sa.Column(
            'bank_billing_method',
            sa.Enum('ANALYSIS', 'HARD_CHARGE', 'EXEMPT', name='bank_billing_method', native_enum=False, length=20),
            nullable=False, server_default='EXEMPT',
        ))
        batch_op.add_column(sa.Column('bank_billing_account_id', sa.Uuid(), nullable=True))

    # Existing "Admin" groups get the new permissions (new tenants' Admin gets them from
    # the live catalog).
    bind = op.get_bind()
    groups = sa.table('security_group', sa.column('id', sa.Uuid()), sa.column('name', sa.String()), sa.column('permissions', sa.JSON()))
    for group_id, permissions in bind.execute(sa.select(groups.c.id, groups.c.permissions).where(groups.c.name == 'Admin')).all():
        missing = [p for p in ('billing:read', 'billing:manage') if p not in (permissions or [])]
        if missing:
            bind.execute(sa.update(groups).where(groups.c.id == group_id).values(permissions=[*(permissions or []), *missing]))

    if bind.dialect.name == 'postgresql':
        for table in _RLS_TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        for table in _RLS_TABLES:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('bank_billing_account_id')
        batch_op.drop_column('bank_billing_method')
        batch_op.drop_column('customer_default_billing_method')
    op.drop_table('customer_fee_override')
    op.drop_table('customer_billing_terms')
    op.drop_table('billing_fee_item')
