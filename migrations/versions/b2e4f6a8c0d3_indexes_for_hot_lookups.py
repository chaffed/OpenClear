# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""indexes for the lookups that run on every presented item, and for the work queue

Revision ID: b2e4f6a8c0d3
Revises: a1d9e3f5b7c2
Create Date: 2026-09-24 14:00:00.000000

SCALE_PLAN.md S2. On Postgres these are built with a plain CREATE INDEX, which blocks
writes to each table while it builds: on a large existing database, run this upgrade in a
quiet window (or create the same indexes CONCURRENTLY by hand first; the names match, so
this migration can then be stamped).
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b2e4f6a8c0d3'
down_revision: Union[str, None] = 'a1d9e3f5b7c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = [
    ('ix_paid_item_tenant_account_check', 'paid_item', ['tenant_id', 'account_id', 'check_number']),
    ('ix_stop_payment_tenant_account_check', 'stop_payment', ['tenant_id', 'account_id', 'check_number']),
    ('ix_exception_item_tenant_status_created', 'exception_item', ['tenant_id', 'status', 'created_at']),
    ('ix_exception_item_status_deadline', 'exception_item', ['status', 'decision_deadline']),
    ('ix_ach_transaction_tenant_account_originator_date', 'ach_transaction', ['tenant_id', 'account_id', 'originator_id', 'effective_date']),
    ('ix_ach_authorization_rule_tenant_account_originator', 'ach_authorization_rule', ['tenant_id', 'account_id', 'originator_id']),
    ('ix_notification_status_created', 'notification', ['status', 'created_at']),
]


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
