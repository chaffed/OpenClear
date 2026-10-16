# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Customers' own API clients (MOBILE_PLAN.md R4)

Revision ID: e8a0c2e4f6b9
Revises: d7f9b1c3e5a8
Create Date: 2026-10-16 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e8a0c2e4f6b9'
down_revision: Union[str, None] = 'd7f9b1c3e5a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('customer') as batch_op:
        batch_op.add_column(sa.Column('self_manages_api_clients', sa.Boolean(), server_default='0', nullable=False))
    with op.batch_alter_table('api_client') as batch_op:
        batch_op.add_column(sa.Column('managed_by', sa.String(length=10), server_default='bank', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('api_client') as batch_op:
        batch_op.drop_column('managed_by')
    with op.batch_alter_table('customer') as batch_op:
        batch_op.drop_column('self_manages_api_clients')
