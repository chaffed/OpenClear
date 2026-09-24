# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""audit_log_entry: a per-bank sequence number, unique, so concurrent writers can't fork the chain

Revision ID: f7b2d4e6a8c1
Revises: e5a1c7d3b9f2
Create Date: 2026-09-24 10:00:00.000000

Existing entries are numbered by following the chain itself (each entry's prev_entry_hash
points at the one before), not by timestamp, which can tie. If a bank's chain has already
forked, the branches are numbered in timestamp order; verify_chain still reports the fork.
"""
from collections import defaultdict
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7b2d4e6a8c1'
down_revision: Union[str, None] = 'e5a1c7d3b9f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_entries = sa.table(
    'audit_log_entry',
    sa.column('id', sa.Uuid()),
    sa.column('tenant_id', sa.Uuid()),
    sa.column('occurred_at', sa.DateTime(timezone=True)),
    sa.column('prev_entry_hash', sa.String()),
    sa.column('entry_hash', sa.String()),
    sa.column('sequence', sa.BigInteger()),
)


def _chain_order(rows) -> list:
    """Row ids in chain order: start at the entry with no predecessor and follow the
    links. Anything unreachable (a fork's other branch, or orphans) goes at the end in
    timestamp order, so every row still gets a number."""
    by_prev = defaultdict(list)
    for row in sorted(rows, key=lambda r: (r.occurred_at, str(r.id))):
        by_prev[row.prev_entry_hash].append(row)
    ordered, seen = [], set()
    frontier = list(by_prev.get(None, []))
    while frontier:
        row = frontier.pop(0)
        if row.id in seen:
            continue
        seen.add(row.id)
        ordered.append(row.id)
        frontier = by_prev.get(row.entry_hash, []) + frontier
    ordered += [r.id for r in sorted(rows, key=lambda r: (r.occurred_at, str(r.id))) if r.id not in seen]
    return ordered


def upgrade() -> None:
    with op.batch_alter_table('audit_log_entry') as batch_op:
        batch_op.add_column(sa.Column('sequence', sa.BigInteger(), nullable=True))

    bind = op.get_bind()
    tenant_ids = [row[0] for row in bind.execute(sa.select(_entries.c.tenant_id).distinct())]
    for tenant_id in tenant_ids:
        rows = bind.execute(
            sa.select(_entries.c.id, _entries.c.occurred_at, _entries.c.prev_entry_hash, _entries.c.entry_hash)
            .where(_entries.c.tenant_id == tenant_id)
        ).all()
        for number, row_id in enumerate(_chain_order(rows), start=1):
            bind.execute(sa.update(_entries).where(_entries.c.id == row_id).values(sequence=number))

    with op.batch_alter_table('audit_log_entry') as batch_op:
        batch_op.alter_column('sequence', existing_type=sa.BigInteger(), nullable=False)
        batch_op.create_unique_constraint('uq_audit_log_entry_tenant_sequence', ['tenant_id', 'sequence'])


def downgrade() -> None:
    with op.batch_alter_table('audit_log_entry') as batch_op:
        batch_op.drop_constraint('uq_audit_log_entry_tenant_sequence', type_='unique')
        batch_op.drop_column('sequence')
