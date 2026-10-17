# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""updated_at on CUEC mappings, attestations and control deviations, so a GRC tool can fetch
what changed (CONTROLS_PLAN.md CT5)

Revision ID: d5f7a9b1c3e4
Revises: c4e6a8b0d2f3
Create Date: 2026-10-17 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5f7a9b1c3e4'
down_revision: Union[str, None] = 'c4e6a8b0d2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Each table, and the column its existing rows' updated_at starts from.
_TABLES = {'cuec_mapping': 'created_at', 'cuec_attestation': 'prepared_at', 'control_deviation': 'opened_at'}


def upgrade() -> None:
    for table, start_from in _TABLES.items():
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'),
                                          nullable=False))
        op.execute(f'UPDATE "{table}" SET updated_at = {start_from}')
        op.create_index(f'ix_{table}_tenant_id_updated_at', table, ['tenant_id', 'updated_at'])


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f'ix_{table}_tenant_id_updated_at', table_name=table)
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column('updated_at')
