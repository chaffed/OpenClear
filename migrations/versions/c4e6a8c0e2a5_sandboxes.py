# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Vendor sandboxes: tenant.is_sandbox, sandbox_expires_at, sandbox_contact_email (SDK_PLAN.md P4)

Revision ID: c4e6a8c0e2a5
Revises: b3d5f7a9c1e4
Create Date: 2026-10-09 05:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4e6a8c0e2a5'
down_revision: Union[str, None] = 'b3d5f7a9c1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('is_sandbox', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('sandbox_expires_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('sandbox_contact_email', sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('sandbox_contact_email')
        batch_op.drop_column('sandbox_expires_at')
        batch_op.drop_column('is_sandbox')
