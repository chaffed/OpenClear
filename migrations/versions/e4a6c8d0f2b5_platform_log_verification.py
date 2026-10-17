# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The provider's action log verifications (DUAL_CONTROL_PLAN.md DC6)

Revision ID: e4a6c8d0f2b5
Revises: d3f5b7c9e1a3
Create Date: 2026-10-17 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e4a6c8d0f2b5'
down_revision: Union[str, None] = 'd3f5b7c9e1a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No bank's data and nothing from the log, only whether it held: every bank's check reads it, so no RLS.
    op.create_table(
        'platform_log_verification',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('intact', sa.Boolean(), nullable=False),
        sa.Column('checked', sa.BigInteger(), nullable=False),
        sa.Column('broken_at_sequence', sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_platform_log_verification')),
    )
    op.create_index(op.f('ix_platform_log_verification_verified_at'), 'platform_log_verification', ['verified_at'])


def downgrade() -> None:
    op.drop_index(op.f('ix_platform_log_verification_verified_at'), table_name='platform_log_verification')
    op.drop_table('platform_log_verification')
