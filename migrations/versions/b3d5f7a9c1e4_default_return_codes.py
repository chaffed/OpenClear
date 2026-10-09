# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The default ACH return reasons get their NACHA R codes, where a bank hasn't set one (SDK_PLAN.md P3)

Revision ID: b3d5f7a9c1e4
Revises: a2c4e6b8d0f3
Create Date: 2026-10-09 03:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3d5f7a9c1e4'
down_revision: Union[str, None] = 'a2c4e6b8d0f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CODES = {
    "Insufficient Funds": "R01", "Account Closed": "R02", "No Account/Unable to Locate Account": "R03",
    "Customer Advises Not Authorized": "R10", "Amount Not Same As Indicated": "R11", "Payment Stopped": "R08",
    "Uncollected Funds": "R09",
}


def upgrade() -> None:
    reason = sa.table('ach_return_reason', sa.column('reason_text', sa.String()), sa.column('transaction_code', sa.String()))
    for text, code in _CODES.items():
        op.execute(reason.update().where(reason.c.reason_text == text, reason.c.transaction_code.is_(None)).values(transaction_code=code))


def downgrade() -> None:
    pass  # a code a bank may since rely on isn't taken away
