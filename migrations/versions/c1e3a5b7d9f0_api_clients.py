# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""api_client (OAuth client credentials for a bank's systems) and user.is_service_account
(SDK_PLAN.md P0); existing Admin groups get api_client:manage

Revision ID: c1e3a5b7d9f0
Revises: b9d1f3a5c7e8
Create Date: 2026-10-09 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1e3a5b7d9f0'
down_revision: Union[str, None] = 'b9d1f3a5c7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PERMISSION = 'api_client:manage'


def upgrade() -> None:
    with op.batch_alter_table('user') as batch_op:
        batch_op.add_column(sa.Column('is_service_account', sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        'api_client',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('client_id', sa.String(length=100), nullable=False),
        sa.Column('secret_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('membership_id', sa.Uuid(), nullable=False),
        sa.Column('can_act_for_users', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('secret_rotated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_api_client_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_api_client_user_id_user')),
        sa.ForeignKeyConstraint(['membership_id'], ['tenant_membership.id'], name=op.f('fk_api_client_membership_id_tenant_membership')),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['user.id'], name=op.f('fk_api_client_created_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_api_client')),
        sa.UniqueConstraint('client_id', name=op.f('uq_api_client_client_id')),
    )
    op.create_index(op.f('ix_api_client_tenant_id'), 'api_client', ['tenant_id'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "api_client" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "api_client" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_api_client ON "api_client" USING ({_TENANT})')

    # Existing "Admin" groups get the new permission (new banks' Admin gets it from
    # auth/permissions.py).
    bind = op.get_bind()
    groups = sa.table('security_group', sa.column('id', sa.Uuid()), sa.column('name', sa.String()), sa.column('permissions', sa.JSON()))
    for group_id, permissions in bind.execute(sa.select(groups.c.id, groups.c.permissions).where(groups.c.name == 'Admin')).all():
        if _PERMISSION not in (permissions or []):
            bind.execute(sa.update(groups).where(groups.c.id == group_id).values(permissions=[*(permissions or []), _PERMISSION]))


def downgrade() -> None:
    bind = op.get_bind()
    groups = sa.table('security_group', sa.column('id', sa.Uuid()), sa.column('permissions', sa.JSON()))
    for group_id, permissions in bind.execute(sa.select(groups.c.id, groups.c.permissions)).all():
        if permissions and _PERMISSION in permissions:
            bind.execute(sa.update(groups).where(groups.c.id == group_id).values(permissions=[p for p in permissions if p != _PERMISSION]))
    if bind.dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_api_client ON "api_client"')
    op.drop_index(op.f('ix_api_client_tenant_id'), table_name='api_client')
    op.drop_table('api_client')
    with op.batch_alter_table('user') as batch_op:
        batch_op.drop_column('is_service_account')
