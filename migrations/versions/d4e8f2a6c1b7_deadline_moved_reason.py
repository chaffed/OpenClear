# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""exception_item.deadline_moved_reason (CONTINUITY_PLAN.md BC-D1)

Why an item's decision deadline was moved after it was set: "outage", when OpenClear was down
across its cut-off and the sweep gave it the bank's minimum review time again.

Revision ID: d4e8f2a6c1b7
Revises: c9d5e3f7a2b4
Create Date: 2026-10-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e8f2a6c1b7'
down_revision: Union[str, None] = 'c9d5e3f7a2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('exception_item') as batch:
        batch.add_column(sa.Column('deadline_moved_reason', sa.String(length=30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('exception_item') as batch:
        batch.drop_column('deadline_moved_reason')
