# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""password_reset: one-time links for an administrator's password reset (OWASP_PLAN.md OW3a)

Revision ID: a1c3e5f7b9d2
Revises: e4a6c8d0f2b5
Create Date: 2026-10-18 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c3e5f7b9d2'
down_revision: Union[str, None] = 'e4a6c8d0f2b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'password_reset',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('requested_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_password_reset_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_password_reset_user_id_user')),
        sa.ForeignKeyConstraint(['requested_by_user_id'], ['user.id'], name=op.f('fk_password_reset_requested_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_password_reset')),
        sa.UniqueConstraint('token_hash', name=op.f('uq_password_reset_token_hash')),
    )
    op.create_index(op.f('ix_password_reset_tenant_id'), 'password_reset', ['tenant_id'])
    op.create_index(op.f('ix_password_reset_user_id'), 'password_reset', ['user_id'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "password_reset" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "password_reset" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_password_reset ON "password_reset" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_password_reset ON "password_reset"')
    op.drop_table('password_reset')
