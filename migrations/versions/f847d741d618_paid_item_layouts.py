# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""File layouts read posted checks too (PAID_ITEM_LAYOUTS_PLAN.md PIL1)

import_layout.kind was always "issued_items"; it can now be "paid_items", for the posted-check
file a bank's core sends. A check constraint keeps it to the two.

Revision ID: f847d741d618
Revises: 5b8e2d4f6a1c
Create Date: 2026-10-22
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'f847d741d618'
down_revision: Union[str, None] = '5b8e2d4f6a1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('import_layout') as batch_op:
        batch_op.create_check_constraint(op.f('ck_import_layout_kind'), "kind IN ('issued_items', 'paid_items')")


def downgrade() -> None:
    op.execute("DELETE FROM import_layout WHERE kind = 'paid_items'")
    with op.batch_alter_table('import_layout') as batch_op:
        batch_op.drop_constraint(op.f('ck_import_layout_kind'), type_='check')
