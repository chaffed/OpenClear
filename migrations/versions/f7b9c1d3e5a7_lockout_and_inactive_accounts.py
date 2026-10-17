# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A bank's own lockout settings, and deactivating and removing inactive access

Revision ID: f7b9c1d3e5a7
Revises: e6a8b0c2d4f5
Create Date: 2026-10-17 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7b9c1d3e5a7'
down_revision: Union[str, None] = 'e6a8b0c2d4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('login_max_failed_attempts', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('login_lockout_minutes', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('lockout_until_unlocked', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('deactivate_inactive', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('deactivate_inactive_days', sa.Integer(), server_default='90', nullable=False))
        batch_op.add_column(sa.Column('remove_inactive', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('remove_inactive_days', sa.Integer(), server_default='365', nullable=False))
    with op.batch_alter_table('tenant_membership') as batch_op:
        batch_op.add_column(sa.Column('last_active_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('inactivity_warned_at', sa.DateTime(timezone=True), nullable=True))
    # Until now only the person's last sign-in anywhere was kept: start each membership from it.
    op.execute('UPDATE tenant_membership SET last_active_at = (SELECT "user".last_login_at FROM "user" WHERE "user".id = tenant_membership.user_id)')


def downgrade() -> None:
    with op.batch_alter_table('tenant_membership') as batch_op:
        batch_op.drop_column('inactivity_warned_at')
        batch_op.drop_column('last_active_at')
    with op.batch_alter_table('tenant') as batch_op:
        for column in ('remove_inactive_days', 'remove_inactive', 'deactivate_inactive_days', 'deactivate_inactive',
                       'lockout_until_unlocked', 'login_lockout_minutes', 'login_max_failed_attempts'):
            batch_op.drop_column(column)
