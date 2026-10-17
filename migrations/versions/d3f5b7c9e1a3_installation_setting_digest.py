# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The installation's acknowledged settings digest (DUAL_CONTROL_PLAN.md DC-D14)

Revision ID: d3f5b7c9e1a3
Revises: c2e4a6b8d0f1
Create Date: 2026-10-18 17:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3f5b7c9e1a3'
down_revision: Union[str, None] = 'c2e4a6b8d0f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No bank's data and no secrets (each setting as a digest): every bank's check reads it, so no RLS.
    op.create_table(
        'installation_setting_digest',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('digest', sa.String(length=64), nullable=False),
        sa.Column('settings', sa.JSON(), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('acknowledged_by_user_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['acknowledged_by_user_id'], ['user.id'], name=op.f('fk_installation_setting_digest_acknowledged_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_installation_setting_digest')),
    )


def downgrade() -> None:
    op.drop_table('installation_setting_digest')
