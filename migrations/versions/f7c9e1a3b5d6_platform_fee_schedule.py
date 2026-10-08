# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""billing: the platform operator's fee schedule, for invoicing banks (BILLING_PLAN.md B4)

Revision ID: f7c9e1a3b5d6
Revises: e6b8d0f2a4c5
Create Date: 2026-10-08 20:00:00.000000

No tenant and no RLS: it belongs to the operator, reached only through a platform API key
with the "billing" scope.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7c9e1a3b5d6'
down_revision: Union[str, None] = 'e6b8d0f2a4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_fee_item',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=False),
        sa.Column('metric', sa.String(length=60), nullable=False),
        sa.Column('unit_price', sa.Numeric(12, 4), nullable=False),
        sa.Column('tiers', sa.JSON(), nullable=True),
        sa.Column('minimum_charge', sa.Numeric(12, 2), nullable=True),
        sa.Column('afp_code', sa.String(length=16), nullable=True),
        sa.Column('bank_service_code', sa.String(length=32), nullable=True),
        sa.Column('taxable', sa.Boolean(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('effective_from', sa.Date(), nullable=True),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_platform_fee_item')),
        sa.UniqueConstraint('code', name=op.f('uq_platform_fee_item_code')),
    )


def downgrade() -> None:
    op.drop_table('platform_fee_item')
