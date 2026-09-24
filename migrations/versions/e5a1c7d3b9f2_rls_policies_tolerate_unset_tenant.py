# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""RLS policies: treat an empty tenant setting as "no tenant" instead of failing

Revision ID: e5a1c7d3b9f2
Revises: c3e8f1a5d9b2
Create Date: 2026-09-23 22:00:00.000000

Once a pooled connection has run one transaction that set app.current_tenant_id,
current_setting('app.current_tenant_id', true) returns '' (not NULL) for the rest of that
connection's life. The original policies cast that straight to uuid, so any later query on
an RLS table without a tenant set failed with "invalid input syntax for type uuid" instead
of matching no rows. NULLIF(..., '') makes it match no rows, which is what the policy meant.
Postgres only; a no-op elsewhere.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5a1c7d3b9f2'
down_revision: Union[str, None] = 'c3e8f1a5d9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "tenant_id = current_setting('app.current_tenant_id', true)::uuid"
_NEW = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def _tenant_isolation_policies() -> list[tuple[str, str]]:
    # Every table that has one, rather than a hard-coded list: several migrations added
    # their own tables' policies over time.
    rows = op.get_bind().execute(
        sa.text("SELECT tablename, policyname FROM pg_policies WHERE schemaname = current_schema() AND policyname LIKE 'tenant_isolation_%'")
    )
    return [(row.tablename, row.policyname) for row in rows]


def _rewrite(expression: str) -> None:
    for table, policy in _tenant_isolation_policies():
        op.execute(f'DROP POLICY {policy} ON "{table}"')
        op.execute(f'CREATE POLICY {policy} ON "{table}" USING ({expression})')


def upgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        _rewrite(_NEW)


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        _rewrite(_OLD)
