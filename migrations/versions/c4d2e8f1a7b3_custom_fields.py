# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom fields (plans/CUSTOM_FIELDS_PLAN.md CF1)

custom_field and custom_field_value (a bank's fields on its customers and accounts, and their
values) have row-level security. platform_custom_field and platform_custom_field_value (the
service provider's fields on banks) have no tenant_id: a bank's session gets no policy that could
show it a hidden value, and bank offboarding doesn't wipe them (CF-D9).

Revision ID: c4d2e8f1a7b3
Revises: b3f1c7a9e2d5
Create Date: 2026-10-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4d2e8f1a7b3'
down_revision: Union[str, None] = 'b3f1c7a9e2d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def _definition_columns() -> list[sa.Column]:
    return [
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('field_type', sa.String(length=10), nullable=False),
        sa.Column('max_length', sa.Integer(), nullable=True),
        sa.Column('options', sa.JSON(), nullable=True),
        sa.Column('required', sa.Boolean(), nullable=False),
        sa.Column('unique', sa.Boolean(), nullable=False),
        sa.Column('help_text', sa.String(length=500), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _value_columns() -> list[sa.Column]:
    return [
        sa.Column('text_value', sa.String(length=255), nullable=True),
        sa.Column('integer_value', sa.BigInteger(), nullable=True),
        sa.Column('decimal_value', sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column('date_value', sa.Date(), nullable=True),
        sa.Column('boolean_value', sa.Boolean(), nullable=True),
        sa.Column('unique_text', sa.String(length=255), nullable=True),
        sa.Column('updated_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'custom_field',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('owner', sa.String(length=10), nullable=False),
        sa.Column('shown_to_customer', sa.Boolean(), nullable=False),
        sa.Column('show_in_list', sa.Boolean(), nullable=False),
        *_definition_columns(),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_custom_field_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['user.id'], name=op.f('fk_custom_field_created_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_custom_field')),
        sa.UniqueConstraint('tenant_id', 'owner', 'key', name='uq_custom_field_key'),
    )
    op.create_index(op.f('ix_custom_field_tenant_id'), 'custom_field', ['tenant_id'], unique=False)
    op.create_table(
        'custom_field_value',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('field_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=True),
        sa.Column('account_id', sa.Uuid(), nullable=True),
        *_value_columns(),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_custom_field_value_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['field_id'], ['custom_field.id'], name=op.f('fk_custom_field_value_field_id_custom_field')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_custom_field_value_customer_id_customer')),
        sa.ForeignKeyConstraint(['account_id'], ['account.id'], name=op.f('fk_custom_field_value_account_id_account')),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['user.id'], name=op.f('fk_custom_field_value_updated_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_custom_field_value')),
        sa.UniqueConstraint('field_id', 'customer_id', name='uq_custom_field_value_customer'),
        sa.UniqueConstraint('field_id', 'account_id', name='uq_custom_field_value_account'),
        sa.UniqueConstraint('field_id', 'unique_text', name='uq_custom_field_value_unique'),
    )
    for column in ('tenant_id', 'field_id', 'customer_id', 'account_id'):
        op.create_index(op.f(f'ix_custom_field_value_{column}'), 'custom_field_value', [column], unique=False)
    op.create_index('ix_custom_field_value_text', 'custom_field_value', ['field_id', 'text_value'], unique=False)
    op.create_table(
        'platform_custom_field',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('shown_to_bank', sa.Boolean(), nullable=False),
        *_definition_columns(),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['user.id'], name=op.f('fk_platform_custom_field_created_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_platform_custom_field')),
        sa.UniqueConstraint('key', name='uq_platform_custom_field_key'),
    )
    op.create_table(
        'platform_custom_field_value',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('field_id', sa.Uuid(), nullable=False),
        sa.Column('bank_id', sa.Uuid(), nullable=False),
        *_value_columns(),
        sa.ForeignKeyConstraint(['field_id'], ['platform_custom_field.id'],
                                name=op.f('fk_platform_custom_field_value_field_id_platform_custom_field')),
        sa.ForeignKeyConstraint(['bank_id'], ['tenant.id'], name=op.f('fk_platform_custom_field_value_bank_id_tenant')),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['user.id'], name=op.f('fk_platform_custom_field_value_updated_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_platform_custom_field_value')),
        sa.UniqueConstraint('field_id', 'bank_id', name='uq_platform_custom_field_value_bank'),
        sa.UniqueConstraint('field_id', 'unique_text', name='uq_platform_custom_field_value_unique'),
    )
    op.create_index(op.f('ix_platform_custom_field_value_field_id'), 'platform_custom_field_value', ['field_id'], unique=False)
    op.create_index(op.f('ix_platform_custom_field_value_bank_id'), 'platform_custom_field_value', ['bank_id'], unique=False)
    if op.get_bind().dialect.name == 'postgresql':
        for table in ('custom_field', 'custom_field_value'):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    op.drop_index(op.f('ix_platform_custom_field_value_bank_id'), table_name='platform_custom_field_value')
    op.drop_index(op.f('ix_platform_custom_field_value_field_id'), table_name='platform_custom_field_value')
    op.drop_table('platform_custom_field_value')
    op.drop_table('platform_custom_field')
    op.drop_index('ix_custom_field_value_text', table_name='custom_field_value')
    for column in ('account_id', 'customer_id', 'field_id', 'tenant_id'):
        op.drop_index(op.f(f'ix_custom_field_value_{column}'), table_name='custom_field_value')
    op.drop_table('custom_field_value')
    op.drop_index(op.f('ix_custom_field_tenant_id'), table_name='custom_field')
    op.drop_table('custom_field')
