# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Support access granted by a bank, and platform staff (MASTER_PLAN.md M6)

Revision ID: f3b5d7f9b1c4
Revises: e2a4c6e8a0b3
Create Date: 2026-10-12 06:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3b5d7f9b1c4'
down_revision: Union[str, None] = 'e2a4c6e8a0b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('user') as batch_op:
        batch_op.add_column(sa.Column('platform_role', sa.String(length=20), nullable=True))
    op.create_table(
        'support_access_grant',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('support_user_id', sa.Uuid(), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('write_access', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('granted_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('approved_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_support_access_grant_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['support_user_id'], ['user.id'], name=op.f('fk_support_access_grant_support_user_id_user')),
        sa.ForeignKeyConstraint(['granted_by_user_id'], ['user.id'], name=op.f('fk_support_access_grant_granted_by_user_id_user')),
        sa.ForeignKeyConstraint(['approved_by_user_id'], ['user.id'], name=op.f('fk_support_access_grant_approved_by_user_id_user')),
        sa.ForeignKeyConstraint(['ended_by_user_id'], ['user.id'], name=op.f('fk_support_access_grant_ended_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_support_access_grant')),
    )
    op.create_index(op.f('ix_support_access_grant_tenant_id'), 'support_access_grant', ['tenant_id'])
    op.create_index(op.f('ix_support_access_grant_support_user_id'), 'support_access_grant', ['support_user_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_support_access_grant_support_user_id'), table_name='support_access_grant')
    op.drop_index(op.f('ix_support_access_grant_tenant_id'), table_name='support_access_grant')
    op.drop_table('support_access_grant')
    with op.batch_alter_table('user') as batch_op:
        batch_op.drop_column('platform_role')
