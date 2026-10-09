# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""tenant_extension.config_encrypted: a notification channel's settings per bank (PLUGIN_PLAN.md PL3)

Revision ID: b7d9f1c3e5a7
Revises: a6c8e0b2d4f5
Create Date: 2026-10-08 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d9f1c3e5a7'
down_revision: Union[str, None] = 'a6c8e0b2d4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenant_extension') as batch_op:
        batch_op.add_column(sa.Column('config_encrypted', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tenant_extension') as batch_op:
        batch_op.drop_column('config_encrypted')
