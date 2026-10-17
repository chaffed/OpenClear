# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A bank can ask its customers to attest their CUECs each year (CONTROLS_PLAN.md CT-D13)

Revision ID: e6a8b0c2d4f5
Revises: d5f7a9b1c3e4
Create Date: 2026-10-17 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e6a8b0c2d4f5'
down_revision: Union[str, None] = 'd5f7a9b1c3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('ask_customers_to_attest', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('customers_asked_for_year', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('customers_asked_for_year')
        batch_op.drop_column('ask_customers_to_attest')
