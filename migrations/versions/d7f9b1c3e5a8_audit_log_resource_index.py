# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The action log by resource: an exception's history on its page (MOBILE_PLAN.md R2)

Revision ID: d7f9b1c3e5a8
Revises: c6e8a0c2e4f7
Create Date: 2026-10-13 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd7f9b1c3e5a8'
down_revision: Union[str, None] = 'c6e8a0c2e4f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_audit_log_entry_tenant_resource', 'audit_log_entry', ['tenant_id', 'resource_id'])


def downgrade() -> None:
    op.drop_index('ix_audit_log_entry_tenant_resource', table_name='audit_log_entry')
