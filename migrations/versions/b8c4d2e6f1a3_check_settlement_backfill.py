# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Checks paid or returned by a decision are settled (REVIEW_1_33_PLAN.md RV-D1)

Until 1.34.0 only a clean match set a paid item's settlement and its issued item's status, so a
check decided *pay* (every check on a reverse positive pay account) stayed pending and could be
presented again without being a duplicate. decision_service.lock now settles the check; this
brings every decision already final into line: its paid item is paid or returned, and a paid
check's outstanding issued item is paid. Reversed paid items and voided issued items are left as
they are.

Revision ID: b8c4d2e6f1a3
Revises: e7a3b9d5c2f1
Create Date: 2026-10-31
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8c4d2e6f1a3'
down_revision: Union[str, None] = 'e7a3b9d5c2f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Enum columns store the member names (native_enum=False, SQLAlchemy's default lookup).
_DECIDED = """
    SELECT e.{column} FROM exception_item e JOIN decision d ON d.exception_item_id = e.id
    WHERE e.tenant_id = :t AND e.network_code = 'check' AND d.locked_at IS NOT NULL AND d.outcome = :outcome
      AND e.{column} IS NOT NULL
"""


def _each_bank(bind):
    postgres = bind.dialect.name == 'postgresql'
    for (tenant_id,) in bind.execute(sa.text('SELECT id FROM tenant')).all():
        if postgres:  # row-level security is forced on paid_item and issued_item
            bind.execute(sa.text("SELECT set_config('app.current_tenant_id', :t, true)"), {'t': str(tenant_id)})
        yield str(tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id)))


def upgrade() -> None:
    bind = op.get_bind()
    for tenant_id in _each_bank(bind):
        for outcome, settlement in (('PAY', 'PAID'), ('RETURN', 'RETURNED')):
            bind.execute(sa.text(
                "UPDATE paid_item SET settlement_status = :s WHERE tenant_id = :t AND settlement_status = 'PENDING' AND id IN ("
                + _DECIDED.format(column='source_item_id') + ")"), {'s': settlement, 't': tenant_id, 'outcome': outcome})
        bind.execute(sa.text(
            "UPDATE issued_item SET status = 'PAID' WHERE tenant_id = :t AND status IN ('OUTSTANDING', 'STALE', 'STOPPED') AND id IN ("
            + _DECIDED.format(column='related_reference_id') + ")"), {'t': tenant_id, 'outcome': 'PAY'})


def downgrade() -> None:
    pass  # a settled check stays settled: the decisions behind these rows are final
