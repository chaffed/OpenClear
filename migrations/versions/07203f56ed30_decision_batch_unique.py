# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Two decision files never start at the same decision (FULL_REVIEW_PLAN.md FR2, R3)

Revision ID: 07203f56ed30
Revises: f6b8d0a2c4e5
Create Date: 2026-10-21

"""
from typing import Sequence, Union

from alembic import op

revision: str = '07203f56ed30'
down_revision: Union[str, None] = 'f6b8d0a2c4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Before this, a scheduled run and an API request at the same moment could each write a
    # batch from the same decision. Keep the first of any such pair, so the constraint can be added.
    op.execute("""
        DELETE FROM decision_batch WHERE id IN (
            SELECT later.id FROM decision_batch later JOIN decision_batch earlier
              ON earlier.tenant_id = later.tenant_id AND earlier.from_sequence = later.from_sequence
             AND (earlier.created_at < later.created_at OR (earlier.created_at = later.created_at AND earlier.id < later.id)))
    """)
    with op.batch_alter_table('decision_batch') as batch_op:
        batch_op.create_unique_constraint('uq_decision_batch_tenant_from_sequence', ['tenant_id', 'from_sequence'])


def downgrade() -> None:
    with op.batch_alter_table('decision_batch') as batch_op:
        batch_op.drop_constraint('uq_decision_batch_tenant_from_sequence', type_='unique')
