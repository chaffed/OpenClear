# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""SAML connections, sso_identity and sso_assertion_seen (SDK_PLAN.md phase F)

Revision ID: c8e0a2d4f6b8
Revises: b7d9f1c3e5a7
Create Date: 2026-10-08 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8e0a2d4f6b8'
down_revision: Union[str, None] = 'b7d9f1c3e5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_TABLES = ('sso_identity', 'sso_assertion_seen')


def upgrade() -> None:
    with op.batch_alter_table('sso_connection') as batch_op:
        batch_op.add_column(sa.Column('protocol', sa.Enum('OIDC', 'SAML', name='sso_protocol', native_enum=False, length=10),
                                      nullable=False, server_default='OIDC'))
        batch_op.alter_column('client_id', existing_type=sa.String(length=255), nullable=True)
        batch_op.alter_column('client_secret_encrypted', existing_type=sa.String(length=1000), nullable=True)
        batch_op.add_column(sa.Column('saml_sso_url', sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column('saml_slo_url', sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column('saml_idp_certificate', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('saml_email_attribute', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('saml_sp_certificate', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('saml_sp_private_key_encrypted', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('allow_idp_initiated', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        'sso_identity',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('connection_id', sa.Uuid(), nullable=False),
        sa.Column('subject', sa.String(length=500), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_sso_identity_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['connection_id'], ['sso_connection.id'], name=op.f('fk_sso_identity_connection_id_sso_connection')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_sso_identity_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_sso_identity')),
        sa.UniqueConstraint('connection_id', 'subject', name='uq_sso_identity_subject'),
    )
    op.create_index(op.f('ix_sso_identity_tenant_id'), 'sso_identity', ['tenant_id'])
    op.create_index(op.f('ix_sso_identity_connection_id'), 'sso_identity', ['connection_id'])
    op.create_index(op.f('ix_sso_identity_user_id'), 'sso_identity', ['user_id'])
    op.create_table(
        'sso_assertion_seen',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('connection_id', sa.Uuid(), nullable=False),
        sa.Column('assertion_id', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_sso_assertion_seen_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['connection_id'], ['sso_connection.id'], name=op.f('fk_sso_assertion_seen_connection_id_sso_connection')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_sso_assertion_seen')),
        sa.UniqueConstraint('connection_id', 'assertion_id', name='uq_sso_assertion_seen'),
    )
    op.create_index(op.f('ix_sso_assertion_seen_tenant_id'), 'sso_assertion_seen', ['tenant_id'])
    op.create_index(op.f('ix_sso_assertion_seen_expires_at'), 'sso_assertion_seen', ['expires_at'])
    if op.get_bind().dialect.name == 'postgresql':
        for table in _TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        for table in _TABLES:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
    op.drop_table('sso_assertion_seen')
    op.drop_table('sso_identity')
    with op.batch_alter_table('sso_connection') as batch_op:
        for column in ('allow_idp_initiated', 'saml_sp_private_key_encrypted', 'saml_sp_certificate', 'saml_email_attribute',
                       'saml_idp_certificate', 'saml_slo_url', 'saml_sso_url', 'protocol'):
            batch_op.drop_column(column)
        batch_op.alter_column('client_secret_encrypted', existing_type=sa.String(length=1000), nullable=False)
        batch_op.alter_column('client_id', existing_type=sa.String(length=255), nullable=False)
