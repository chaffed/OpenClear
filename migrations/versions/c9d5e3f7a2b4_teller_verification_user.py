# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""teller_verification.user_id: whose token asked (REVIEW_1_33_PLAN.md T6)

A teller platform's verifications carry its API client; a person given check:verify had nothing
to be counted by, so the probing detector couldn't see them. Null for rows before this.

Revision ID: c9d5e3f7a2b4
Revises: b8c4d2e6f1a3
Create Date: 2026-10-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d5e3f7a2b4'
down_revision: Union[str, None] = 'b8c4d2e6f1a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('teller_verification') as batch:
        batch.add_column(sa.Column('user_id', sa.Uuid(), nullable=True))
        batch.create_foreign_key('fk_teller_verification_user_id', 'user', ['user_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('teller_verification') as batch:
        batch.drop_constraint('fk_teller_verification_user_id', type_='foreignkey')
        batch.drop_column('user_id')
