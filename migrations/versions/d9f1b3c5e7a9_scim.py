# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""SCIM 2.0 provisioning: scim_user, scim_group, scim_group_member, sso_connection.scim_token_* (SDK_PLAN.md phase F)

Revision ID: d9f1b3c5e7a9
Revises: c8e0a2d4f6b8
Create Date: 2026-10-08 23:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd9f1b3c5e7a9'
down_revision: Union[str, None] = 'c8e0a2d4f6b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_TABLES = ('scim_user', 'scim_group', 'scim_group_member')


def _stamps():
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    ]


def upgrade() -> None:
    with op.batch_alter_table('sso_connection') as batch_op:
        batch_op.add_column(sa.Column('scim_token_hash', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('scim_token_created_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'scim_user',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('connection_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('user_name', sa.String(length=255), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=True),
        sa.Column('display_name', sa.String(length=255), nullable=True),
        sa.Column('given_name', sa.String(length=255), nullable=True),
        sa.Column('family_name', sa.String(length=255), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        *_stamps(),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_scim_user_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['connection_id'], ['sso_connection.id'], name=op.f('fk_scim_user_connection_id_sso_connection')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_scim_user_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_scim_user')),
        sa.UniqueConstraint('connection_id', 'user_name', name='uq_scim_user_user_name'),
        sa.UniqueConstraint('connection_id', 'user_id', name='uq_scim_user_user'),
    )
    for column in ('tenant_id', 'connection_id', 'user_id'):
        op.create_index(op.f(f'ix_scim_user_{column}'), 'scim_user', [column])
    op.create_table(
        'scim_group',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('connection_id', sa.Uuid(), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=True),
        *_stamps(),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_scim_group_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['connection_id'], ['sso_connection.id'], name=op.f('fk_scim_group_connection_id_sso_connection')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_scim_group')),
        sa.UniqueConstraint('connection_id', 'display_name', name='uq_scim_group_display_name'),
    )
    for column in ('tenant_id', 'connection_id'):
        op.create_index(op.f(f'ix_scim_group_{column}'), 'scim_group', [column])
    op.create_table(
        'scim_group_member',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('group_id', sa.Uuid(), nullable=False),
        sa.Column('scim_user_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_scim_group_member_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['group_id'], ['scim_group.id'], name=op.f('fk_scim_group_member_group_id_scim_group')),
        sa.ForeignKeyConstraint(['scim_user_id'], ['scim_user.id'], name=op.f('fk_scim_group_member_scim_user_id_scim_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_scim_group_member')),
        sa.UniqueConstraint('group_id', 'scim_user_id', name='uq_scim_group_member'),
    )
    for column in ('tenant_id', 'group_id', 'scim_user_id'):
        op.create_index(op.f(f'ix_scim_group_member_{column}'), 'scim_group_member', [column])
    if op.get_bind().dialect.name == 'postgresql':
        for table in _TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        for table in _TABLES:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
    op.drop_table('scim_group_member')
    op.drop_table('scim_group')
    op.drop_table('scim_user')
    with op.batch_alter_table('sso_connection') as batch_op:
        batch_op.drop_column('scim_token_created_at')
        batch_op.drop_column('scim_token_hash')
