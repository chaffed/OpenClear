# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Dual control for settings changes (DUAL_CONTROL_PLAN.md DC0, DC1): change_request for every
scope, and the bank's two switches

Revision ID: a9c1e3f5b7d9
Revises: f7b9c1d3e5a7
Create Date: 2026-10-18 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9c1e3f5b7d9'
down_revision: Union[str, None] = 'f7b9c1d3e5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_NO_BANK = "NULLIF(current_setting('app.current_tenant_id', true), '') IS NULL"


def upgrade() -> None:
    with op.batch_alter_table('change_request') as batch_op:
        batch_op.alter_column('tenant_id', existing_type=sa.Uuid(), nullable=True)
        batch_op.alter_column('requested_by_user_id', existing_type=sa.Uuid(), nullable=True)
        batch_op.add_column(sa.Column('customer_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('scope', sa.String(length=10), server_default='bank', nullable=False))
        batch_op.add_column(sa.Column('setting', sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column('before', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('after', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('reason', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('requested_with', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('applies_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('decided_reason', sa.Text(), nullable=True))
        batch_op.create_foreign_key('fk_change_request_customer_id_customer', 'customer', ['customer_id'], ['id'])
        batch_op.create_index('ix_change_request_tenant_status', ['tenant_id', 'status'])
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('sensitive_changes_need_approval', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('access_changes_need_approval', sa.Boolean(), server_default='0', nullable=False))
    # A bank on enforce had rules and models wait for a second admin; that now follows this switch (DC1).
    op.execute("UPDATE tenant SET sensitive_changes_need_approval = true WHERE sod_mode = 'enforce'")
    if op.get_bind().dialect.name == "postgresql":
        # The service provider's requests (tenant_id null) are read and written only from a session
        # bound to no bank, the platform's (DC4).
        op.execute('DROP POLICY IF EXISTS tenant_isolation_change_request ON "change_request"')
        op.execute(f'CREATE POLICY tenant_isolation_change_request ON "change_request" '
                   f'USING ({_TENANT} OR (tenant_id IS NULL AND {_NO_BANK})) '
                   f'WITH CHECK ({_TENANT} OR (tenant_id IS NULL AND {_NO_BANK}))')


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS tenant_isolation_change_request ON "change_request"')
        op.execute(f'CREATE POLICY tenant_isolation_change_request ON "change_request" USING ({_TENANT})')
    op.execute("DELETE FROM change_request WHERE tenant_id IS NULL")
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('access_changes_need_approval')
        batch_op.drop_column('sensitive_changes_need_approval')
    with op.batch_alter_table('change_request') as batch_op:
        batch_op.drop_index('ix_change_request_tenant_status')
        batch_op.drop_constraint('fk_change_request_customer_id_customer', type_='foreignkey')
        for column in ('decided_reason', 'applies_at', 'expires_at', 'requested_with', 'reason', 'after', 'before', 'setting', 'scope',
                       'customer_id'):
            batch_op.drop_column(column)
        batch_op.alter_column('requested_by_user_id', existing_type=sa.Uuid(), nullable=False)
        batch_op.alter_column('tenant_id', existing_type=sa.Uuid(), nullable=False)
