# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A check's return deadline, and banks open on Federal Reserve holidays (REGULATORY_PLAN.md RG1)

exception_item.return_deadline: a check's midnight deadline, which its decision deadline is kept
within. tenant.open_on_federal_holidays: those days count as banking days for it. Existing items
keep no return deadline.

Revision ID: 44ed7f9be66a
Revises: 81bc96eb16ef
Create Date: 2026-10-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '44ed7f9be66a'
down_revision: Union[str, None] = '81bc96eb16ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('exception_item') as batch_op:
        batch_op.add_column(sa.Column('return_deadline', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('open_on_federal_holidays', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('open_on_federal_holidays')
    with op.batch_alter_table('exception_item') as batch_op:
        batch_op.drop_column('return_deadline')
