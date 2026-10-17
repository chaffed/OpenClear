# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A second person for customers' sensitive changes (DUAL_CONTROL_PLAN.md DC3)

Revision ID: b1d3f5a7c9e2
Revises: a9c1e3f5b7d9
Create Date: 2026-10-18 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1d3f5a7c9e2'
down_revision: Union[str, None] = 'a9c1e3f5b7d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('customer_changes_need_approval', sa.Boolean(), server_default='0', nullable=False))
    with op.batch_alter_table('customer') as batch_op:
        batch_op.add_column(sa.Column('changes_need_approval', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('customer') as batch_op:
        batch_op.drop_column('changes_need_approval')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('customer_changes_need_approval')
