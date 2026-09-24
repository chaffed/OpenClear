# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""RLS: a signed-in user can see their own memberships in every bank (switching organization)

Revision ID: a1d9e3f5b7c2
Revises: f7b2d4e6a8c1
Create Date: 2026-09-24 12:00:00.000000

"Switch organization" lists the signed-in user's memberships in other banks. Under
enforced RLS the tenant_membership policy only showed the current bank's rows. Reads now
also match rows for app.current_user_id (set by db/tenancy.py for a signed-in request).
Writes stay limited to the current bank (WITH CHECK). Postgres only; a no-op elsewhere.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a1d9e3f5b7c2'
down_revision: Union[str, None] = 'f7b2d4e6a8c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_OWN_USER = "user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def upgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute('DROP POLICY tenant_isolation_tenant_membership ON "tenant_membership"')
    op.execute(
        f'CREATE POLICY tenant_isolation_tenant_membership ON "tenant_membership" '
        f'USING ({_TENANT} OR {_OWN_USER}) WITH CHECK ({_TENANT})'
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute('DROP POLICY tenant_isolation_tenant_membership ON "tenant_membership"')
    op.execute(f'CREATE POLICY tenant_isolation_tenant_membership ON "tenant_membership" USING ({_TENANT})')
