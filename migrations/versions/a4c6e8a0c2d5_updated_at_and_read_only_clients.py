# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""updated_at on the main records, and read-only API clients (docs/guides/bi.md)

Revision ID: a4c6e8a0c2d5
Revises: f3b5d7f9b1c4
Create Date: 2026-10-12 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4c6e8a0c2d5'
down_revision: Union[str, None] = 'f3b5d7f9b1c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that gain updated_at, and whether they have a created_at to start it from.
_TABLES = {
    'exception_item': True, 'paid_item': True, 'ach_transaction': True, 'stop_payment': True,
    'account': False, 'customer': True, 'ach_authorization_rule': True,
}
# The large ones get an index for "changed since" reads.
_INDEXED = ('exception_item', 'paid_item', 'ach_transaction', 'issued_item')


def upgrade() -> None:
    for table, has_created in _TABLES.items():
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'),
                                          nullable=False))
        if has_created:
            op.execute(f'UPDATE "{table}" SET updated_at = created_at WHERE created_at IS NOT NULL')
    for table in _INDEXED:
        op.create_index(f'ix_{table}_tenant_id_updated_at', table, ['tenant_id', 'updated_at'])
    with op.batch_alter_table('api_client') as batch_op:
        batch_op.add_column(sa.Column('read_only', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('api_client') as batch_op:
        batch_op.drop_column('read_only')
    for table in _INDEXED:
        op.drop_index(f'ix_{table}_tenant_id_updated_at', table_name=table)
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column('updated_at')
