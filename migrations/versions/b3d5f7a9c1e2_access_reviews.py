# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Access reviews, file control totals, outside service reviews and the bank's standards
(CONTROLS_PLAN.md CT3)

Revision ID: b3d5f7a9c1e2
Revises: a2c4e6f8b0d1
Create Date: 2026-10-16 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3d5f7a9c1e2'
down_revision: Union[str, None] = 'a2c4e6f8b0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROTECTED = ('access_review', 'access_review_item')


def upgrade() -> None:
    op.create_table(
        'access_review',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=True),
        sa.Column('started_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('item_count', sa.Integer(), nullable=False),
        sa.Column('decisions_sha256', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_access_review_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_access_review_customer_id_customer')),
        sa.ForeignKeyConstraint(['started_by_user_id'], ['user.id'], name=op.f('fk_access_review_started_by_user_id_user')),
        sa.ForeignKeyConstraint(['completed_by_user_id'], ['user.id'], name=op.f('fk_access_review_completed_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_access_review')),
    )
    op.create_index(op.f('ix_access_review_tenant_id'), 'access_review', ['tenant_id'])
    op.create_index(op.f('ix_access_review_customer_id'), 'access_review', ['customer_id'])
    op.create_table(
        'access_review_item',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('review_id', sa.Uuid(), nullable=False),
        sa.Column('membership_id', sa.Uuid(), nullable=False),
        sa.Column('who', sa.String(length=320), nullable=False),
        sa.Column('role', sa.String(length=200), nullable=False),
        sa.Column('scope', sa.String(length=300), nullable=False),
        sa.Column('accounts', sa.String(length=500), nullable=False),
        sa.Column('last_sign_in', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision', sa.String(length=10), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('decided_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_access_review_item_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['review_id'], ['access_review.id'], name=op.f('fk_access_review_item_review_id_access_review')),
        sa.ForeignKeyConstraint(['decided_by_user_id'], ['user.id'], name=op.f('fk_access_review_item_decided_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_access_review_item')),
    )
    op.create_index(op.f('ix_access_review_item_tenant_id'), 'access_review_item', ['tenant_id'])
    op.create_index(op.f('ix_access_review_item_review_id'), 'access_review_item', ['review_id'])
    with op.batch_alter_table('bulk_upload_file') as batch_op:
        batch_op.add_column(sa.Column('control_item_count', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('control_total', sa.Numeric(16, 2), nullable=True))
        batch_op.add_column(sa.Column('loaded_item_count', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('loaded_total', sa.Numeric(16, 2), nullable=True))
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('standard_password_min_length', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('key_rotation_days', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('outside_service_reviews', sa.JSON(), server_default='{}', nullable=False))

    if op.get_bind().dialect.name == 'postgresql':
        for table in _PROTECTED:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        for table in _PROTECTED:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('outside_service_reviews')
        batch_op.drop_column('key_rotation_days')
        batch_op.drop_column('standard_password_min_length')
    with op.batch_alter_table('bulk_upload_file') as batch_op:
        batch_op.drop_column('loaded_total')
        batch_op.drop_column('loaded_item_count')
        batch_op.drop_column('control_total')
        batch_op.drop_column('control_item_count')
    op.drop_index(op.f('ix_access_review_item_review_id'), table_name='access_review_item')
    op.drop_index(op.f('ix_access_review_item_tenant_id'), table_name='access_review_item')
    op.drop_table('access_review_item')
    op.drop_index(op.f('ix_access_review_customer_id'), table_name='access_review')
    op.drop_index(op.f('ix_access_review_tenant_id'), table_name='access_review')
    op.drop_table('access_review')
