# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Sessions, devices, email changes, banks' own email domains (OWASP_PLAN.md OW3c, 1.24.0)

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c0e1
Create Date: 2026-10-18 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3e5a7b9d1f2'
down_revision: Union[str, None] = 'b2d4f6a8c0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'login_session',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('signed_in_with', sa.String(length=20), nullable=True),
        sa.Column('token_version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('user_agent', sa.String(length=300), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_login_session_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_login_session_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_login_session')),
    )
    op.create_index(op.f('ix_login_session_tenant_id'), 'login_session', ['tenant_id'])
    op.create_index(op.f('ix_login_session_user_id'), 'login_session', ['user_id'])
    op.create_table(
        'user_device',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('device_hash', sa.String(length=64), nullable=False),
        sa.Column('user_agent', sa.String(length=300), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_user_device_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_user_device')),
        sa.UniqueConstraint('user_id', 'device_hash', name=op.f('uq_user_device_user_id')),
    )
    op.create_index(op.f('ix_user_device_user_id'), 'user_device', ['user_id'])
    op.create_table(
        'email_change',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=True),
        sa.Column('new_email', sa.String(length=320), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('requested_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_email_change_user_id_user')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_email_change_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['requested_by_user_id'], ['user.id'], name=op.f('fk_email_change_requested_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_email_change')),
        sa.UniqueConstraint('token_hash', name=op.f('uq_email_change_token_hash')),
    )
    op.create_index(op.f('ix_email_change_user_id'), 'email_change', ['user_id'])
    with op.batch_alter_table('customer_email_domain') as batch_op:
        batch_op.alter_column('customer_id', existing_type=sa.Uuid(), nullable=True)
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "login_session" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "login_session" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_login_session ON "login_session" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_login_session ON "login_session"')
    op.execute("DELETE FROM customer_email_domain WHERE customer_id IS NULL")
    with op.batch_alter_table('customer_email_domain') as batch_op:
        batch_op.alter_column('customer_id', existing_type=sa.Uuid(), nullable=False)
    op.drop_table('email_change')
    op.drop_table('user_device')
    op.drop_table('login_session')
