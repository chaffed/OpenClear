# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Customers managing their own people and sign-on (CUSTOMER_ADMIN_PLAN.md)

Revision ID: c6e8a0c2e4f7
Revises: b5d7f9b1d3e6
Create Date: 2026-10-13 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c6e8a0c2e4f7'
down_revision: Union[str, None] = 'b5d7f9b1d3e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_OWN_USER = "user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
# db/tenancy.py::memberships_elsewhere: one named person's memberships in every bank, for one
# query, so "does this person belong anywhere else?" is answered truthfully under RLS.
_PROBED_USER = "user_id = NULLIF(current_setting('app.membership_probe_user', true), '')::uuid"
_PROTECTED = ('user_invitation', 'customer_email_domain')


def upgrade() -> None:
    with op.batch_alter_table('customer') as batch_op:
        batch_op.add_column(sa.Column('self_manages_users', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('self_manages_sso', sa.Boolean(), server_default='0', nullable=False))
    with op.batch_alter_table('security_group') as batch_op:
        batch_op.add_column(sa.Column('offered_to_customers', sa.Boolean(), server_default='0', nullable=False))
    with op.batch_alter_table('sso_connection') as batch_op:
        batch_op.add_column(sa.Column('managed_by', sa.String(length=10), server_default='bank', nullable=False))
    with op.batch_alter_table('notification') as batch_op:
        batch_op.alter_column('recipient_user_id', existing_type=sa.Uuid(), nullable=True)

    op.create_table(
        'user_invitation',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('security_group_id', sa.Uuid(), nullable=False),
        sa.Column('all_accounts', sa.Boolean(), nullable=False),
        sa.Column('account_ids', sa.JSON(), nullable=False),
        sa.Column('require_webauthn', sa.Boolean(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('invited_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('accepted_user_id', sa.Uuid(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_user_invitation_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_user_invitation_customer_id_customer')),
        sa.ForeignKeyConstraint(['security_group_id'], ['security_group.id'], name=op.f('fk_user_invitation_security_group_id_security_group')),
        sa.ForeignKeyConstraint(['invited_by_user_id'], ['user.id'], name=op.f('fk_user_invitation_invited_by_user_id_user')),
        sa.ForeignKeyConstraint(['accepted_user_id'], ['user.id'], name=op.f('fk_user_invitation_accepted_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_user_invitation')),
        sa.UniqueConstraint('token_hash', name=op.f('uq_user_invitation_token_hash')),
    )
    op.create_index(op.f('ix_user_invitation_tenant_id'), 'user_invitation', ['tenant_id'])
    op.create_index(op.f('ix_user_invitation_customer_id'), 'user_invitation', ['customer_id'])

    op.create_table(
        'customer_email_domain',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=False),
        sa.Column('domain', sa.String(length=253), nullable=False),
        sa.Column('verification_token', sa.String(length=64), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_customer_email_domain_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_customer_email_domain_customer_id_customer')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_customer_email_domain')),
        sa.UniqueConstraint('tenant_id', 'domain', name=op.f('uq_customer_email_domain_tenant_id')),
    )
    op.create_index(op.f('ix_customer_email_domain_tenant_id'), 'customer_email_domain', ['tenant_id'])
    op.create_index(op.f('ix_customer_email_domain_customer_id'), 'customer_email_domain', ['customer_id'])

    if op.get_bind().dialect.name == 'postgresql':
        for table in _PROTECTED:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')
        op.execute('DROP POLICY tenant_isolation_tenant_membership ON "tenant_membership"')
        op.execute(
            f'CREATE POLICY tenant_isolation_tenant_membership ON "tenant_membership" '
            f'USING ({_TENANT} OR {_OWN_USER} OR {_PROBED_USER}) WITH CHECK ({_TENANT})'
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY tenant_isolation_tenant_membership ON "tenant_membership"')
        op.execute(
            f'CREATE POLICY tenant_isolation_tenant_membership ON "tenant_membership" '
            f'USING ({_TENANT} OR {_OWN_USER}) WITH CHECK ({_TENANT})'
        )
        for table in _PROTECTED:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
    op.drop_index(op.f('ix_customer_email_domain_customer_id'), table_name='customer_email_domain')
    op.drop_index(op.f('ix_customer_email_domain_tenant_id'), table_name='customer_email_domain')
    op.drop_table('customer_email_domain')
    op.drop_index(op.f('ix_user_invitation_customer_id'), table_name='user_invitation')
    op.drop_index(op.f('ix_user_invitation_tenant_id'), table_name='user_invitation')
    op.drop_table('user_invitation')
    op.execute("DELETE FROM notification WHERE recipient_user_id IS NULL")
    with op.batch_alter_table('notification') as batch_op:
        batch_op.alter_column('recipient_user_id', existing_type=sa.Uuid(), nullable=False)
    with op.batch_alter_table('sso_connection') as batch_op:
        batch_op.drop_column('managed_by')
    with op.batch_alter_table('security_group') as batch_op:
        batch_op.drop_column('offered_to_customers')
    with op.batch_alter_table('customer') as batch_op:
        batch_op.drop_column('self_manages_sso')
        batch_op.drop_column('self_manages_users')
