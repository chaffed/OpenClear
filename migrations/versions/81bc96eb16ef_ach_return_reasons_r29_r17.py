# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""ACH return reasons: R29 and R17 for every bank, and R11's current wording (REGULATORY_PLAN.md RG2)

R29 (a business account's unauthorized debit) and R17 (suspected fraud) are added to each bank
that has no reason with that code. R11 is renamed only where it still has the old default text,
so a bank's own wording is kept.

Revision ID: 81bc96eb16ef
Revises: 4c47d3f3a975
Create Date: 2026-10-22
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '81bc96eb16ef'
down_revision: Union[str, None] = '4c47d3f3a975'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ADDED = (("Corporate Customer Advises Not Authorized", "R29"), ("Suspected Fraud (Questionable)", "R17"))
_R11_OLD = "Amount Not Same As Indicated"
_R11_NEW = "Customer Advises Entry Not in Accordance with the Terms of the Authorization"


def _each_bank(bind):
    postgres = bind.dialect.name == 'postgresql'
    for (tenant_id,) in bind.execute(sa.text('SELECT id FROM tenant')).all():
        if postgres:  # row-level security is forced on ach_return_reason
            bind.execute(sa.text("SELECT set_config('app.current_tenant_id', :t, true)"), {'t': str(tenant_id)})
        yield tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))  # SQLite returns text


def upgrade() -> None:
    bind = op.get_bind()
    reason = sa.table('ach_return_reason', sa.column('id', sa.Uuid()), sa.column('tenant_id', sa.Uuid()),
                      sa.column('reason_text', sa.String()), sa.column('transaction_code', sa.String()),
                      sa.column('is_active', sa.Boolean()))
    for tenant_id in _each_bank(bind):
        held = set(bind.execute(sa.select(reason.c.transaction_code).where(reason.c.tenant_id == tenant_id)).scalars())
        for text, code in _ADDED:
            if code not in held:
                bind.execute(reason.insert().values(id=uuid.uuid4(), tenant_id=tenant_id, reason_text=text, transaction_code=code,
                                                    is_active=True))
        bind.execute(reason.update().where(reason.c.tenant_id == tenant_id, reason.c.reason_text == _R11_OLD)
                     .values(reason_text=_R11_NEW))


def downgrade() -> None:
    pass  # reasons a decision may already reference aren't taken away, and the wording is kept
