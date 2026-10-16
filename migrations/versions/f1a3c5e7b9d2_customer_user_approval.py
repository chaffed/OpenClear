# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank approves new customer users (MOBILE_PLAN.md R6)

Revision ID: f1a3c5e7b9d2
Revises: e8a0c2e4f6b9
Create Date: 2026-10-16 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a3c5e7b9d2'
down_revision: Union[str, None] = 'e8a0c2e4f6b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('approve_customer_users', sa.Boolean(), server_default='0', nullable=False))
    with op.batch_alter_table('customer') as batch_op:
        batch_op.add_column(sa.Column('approve_users', sa.Boolean(), nullable=True))
    with op.batch_alter_table('tenant_membership') as batch_op:
        batch_op.add_column(sa.Column('pending_approval_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tenant_membership') as batch_op:
        batch_op.drop_column('pending_approval_at')
    with op.batch_alter_table('customer') as batch_op:
        batch_op.drop_column('approve_users')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('approve_customer_users')
