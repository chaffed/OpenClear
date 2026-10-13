# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Control status changes, key-pair API clients, per-account access (MASTER_PLAN.md M9)

Revision ID: b5d7f9b1d3e6
Revises: a4c6e8a0c2d5
Create Date: 2026-10-12 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b5d7f9b1d3e6'
down_revision: Union[str, None] = 'a4c6e8a0c2d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROTECTED = ('control_status', 'client_assertion_use', 'membership_account')


def upgrade() -> None:
    op.create_table(
        'control_status',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('key', sa.String(length=60), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_control_status_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_control_status')),
        sa.UniqueConstraint('tenant_id', 'key', name=op.f('uq_control_status_tenant_id')),
    )
    op.create_index(op.f('ix_control_status_tenant_id'), 'control_status', ['tenant_id'])

    with op.batch_alter_table('api_client') as batch_op:
        batch_op.add_column(sa.Column('token_auth_method', sa.String(length=20), server_default='client_secret', nullable=False))
        batch_op.add_column(sa.Column('public_key_pem', sa.Text(), nullable=True))
    op.create_table(
        'client_assertion_use',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('api_client_id', sa.Uuid(), nullable=False),
        sa.Column('jti', sa.String(length=200), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_client_assertion_use_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['api_client_id'], ['api_client.id'], name=op.f('fk_client_assertion_use_api_client_id_api_client')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_client_assertion_use')),
        sa.UniqueConstraint('api_client_id', 'jti', name=op.f('uq_client_assertion_use_api_client_id')),
    )
    op.create_index(op.f('ix_client_assertion_use_tenant_id'), 'client_assertion_use', ['tenant_id'])
    op.create_index(op.f('ix_client_assertion_use_expires_at'), 'client_assertion_use', ['expires_at'])

    with op.batch_alter_table('tenant_membership') as batch_op:
        batch_op.add_column(sa.Column('all_accounts', sa.Boolean(), server_default='1', nullable=False))
    op.create_table(
        'membership_account',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('membership_id', sa.Uuid(), nullable=False),
        sa.Column('account_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_membership_account_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['membership_id'], ['tenant_membership.id'], name=op.f('fk_membership_account_membership_id_tenant_membership')),
        sa.ForeignKeyConstraint(['account_id'], ['account.id'], name=op.f('fk_membership_account_account_id_account')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_membership_account')),
        sa.UniqueConstraint('membership_id', 'account_id', name=op.f('uq_membership_account_membership_id')),
    )
    op.create_index(op.f('ix_membership_account_tenant_id'), 'membership_account', ['tenant_id'])
    op.create_index(op.f('ix_membership_account_membership_id'), 'membership_account', ['membership_id'])

    if op.get_bind().dialect.name == 'postgresql':
        for table in _PROTECTED:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        for table in _PROTECTED:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
    op.drop_index(op.f('ix_membership_account_membership_id'), table_name='membership_account')
    op.drop_index(op.f('ix_membership_account_tenant_id'), table_name='membership_account')
    op.drop_table('membership_account')
    with op.batch_alter_table('tenant_membership') as batch_op:
        batch_op.drop_column('all_accounts')
    op.drop_index(op.f('ix_client_assertion_use_expires_at'), table_name='client_assertion_use')
    op.drop_index(op.f('ix_client_assertion_use_tenant_id'), table_name='client_assertion_use')
    op.drop_table('client_assertion_use')
    with op.batch_alter_table('api_client') as batch_op:
        batch_op.drop_column('public_key_pem')
        batch_op.drop_column('token_auth_method')
    op.drop_index(op.f('ix_control_status_tenant_id'), table_name='control_status')
    op.drop_table('control_status')
