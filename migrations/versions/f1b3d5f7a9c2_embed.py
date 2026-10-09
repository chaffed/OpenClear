# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Embedding: tenant.embed_allowed_origins and embed_launch (SDK_PLAN.md P2)

Revision ID: f1b3d5f7a9c2
Revises: e0a2c4e6a8b1
Create Date: 2026-10-09 02:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1b3d5f7a9c2'
down_revision: Union[str, None] = 'e0a2c4e6a8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('embed_allowed_origins', sa.Text(), nullable=True))
    op.create_table(
        'embed_launch',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('membership_id', sa.Uuid(), nullable=False),
        sa.Column('api_client_id', sa.Uuid(), nullable=True),
        sa.Column('path', sa.String(length=500), nullable=False),
        sa.Column('parent_origin', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_embed_launch_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_embed_launch_user_id_user')),
        sa.ForeignKeyConstraint(['membership_id'], ['tenant_membership.id'], name=op.f('fk_embed_launch_membership_id_tenant_membership')),
        sa.ForeignKeyConstraint(['api_client_id'], ['api_client.id'], name=op.f('fk_embed_launch_api_client_id_api_client')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_embed_launch')),
        sa.UniqueConstraint('code_hash', name=op.f('uq_embed_launch_code_hash')),
    )
    op.create_index(op.f('ix_embed_launch_tenant_id'), 'embed_launch', ['tenant_id'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "embed_launch" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "embed_launch" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_embed_launch ON "embed_launch" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_embed_launch ON "embed_launch"')
    op.drop_table('embed_launch')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('embed_allowed_origins')
