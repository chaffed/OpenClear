# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""tenant.continuity_snapshot_emailed (CONTINUITY_PLAN.md BC-D5)

Whether the bank's daily open-items snapshot is emailed to its incident contacts.

Revision ID: f7a1b5c9d3e2
Revises: e5f9a3b7d2c8
Create Date: 2026-10-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7a1b5c9d3e2'
down_revision: Union[str, None] = 'e5f9a3b7d2c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch:
        batch.add_column(sa.Column('continuity_snapshot_emailed', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('tenant') as batch:
        batch.drop_column('continuity_snapshot_emailed')
