# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""An account's check review: positive pay, or reverse positive pay (REVERSE_POSITIVE_PAY_PLAN.md RPP1)

Every existing account stays on positive pay.

Revision ID: 7f7bc3cb2b34
Revises: f847d741d618
Create Date: 2026-10-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7f7bc3cb2b34'
down_revision: Union[str, None] = 'f847d741d618'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('account') as batch_op:
        batch_op.add_column(sa.Column(
            'check_review_mode', sa.Enum('POSITIVE', 'REVERSE', name='check_review_mode', native_enum=False, length=10),
            nullable=False, server_default='POSITIVE',
        ))


def downgrade() -> None:
    with op.batch_alter_table('account') as batch_op:
        batch_op.drop_column('check_review_mode')
