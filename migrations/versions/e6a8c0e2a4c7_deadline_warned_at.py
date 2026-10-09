# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""exception_item.deadline_warned_at, for the exception.deadline_approaching webhook (SDK_PLAN.md P0)

Revision ID: e6a8c0e2a4c7
Revises: d5f7b9d1f3b6
Create Date: 2026-10-09 07:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e6a8c0e2a4c7'
down_revision: Union[str, None] = 'd5f7b9d1f3b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('exception_item') as batch_op:
        batch_op.add_column(sa.Column('deadline_warned_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('exception_item') as batch_op:
        batch_op.drop_column('deadline_warned_at')
