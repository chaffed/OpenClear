# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""restore_test: a restore drill's signed result (CONTINUITY_PLAN.md BC-D2)

No tenant_id: one row per drill, for the whole installation, so no row-level security policy.

Revision ID: e5f9a3b7d2c8
Revises: d4e8f2a6c1b7
Create Date: 2026-10-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f9a3b7d2c8'
down_revision: Union[str, None] = 'd4e8f2a6c1b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'restore_test',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('run_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('ok', sa.Boolean(), nullable=False),
        sa.Column('backup_completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source', sa.String(length=1000), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.Column('certificate', sa.JSON(), nullable=True),
        sa.Column('certificate_sha256', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_restore_test_run_at', 'restore_test', ['run_at'])


def downgrade() -> None:
    op.drop_index('ix_restore_test_run_at', table_name='restore_test')
    op.drop_table('restore_test')
