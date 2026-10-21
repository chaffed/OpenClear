# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A built-in "No access yet" group in every bank (UI_PLAN.md UI-D14)

People a bank invites join it: signed in, with nothing else, until someone other than whoever
invited them gives them a role. It can't be edited or given permissions.

Revision ID: f6b8d0a2c4e5
Revises: e5a7c9b1d3f4
Create Date: 2026-10-20 12:00:00.000000
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6b8d0a2c4e5'
down_revision: Union[str, None] = 'e5a7c9b1d3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('security_group') as batch_op:
        batch_op.add_column(sa.Column('system_role', sa.String(length=30), nullable=True))
    bind = op.get_bind()
    postgres = bind.dialect.name == 'postgresql'
    for (tenant_id,) in bind.execute(sa.text('SELECT id FROM tenant')).all():
        if postgres:  # row-level security is forced on security_group
            bind.execute(sa.text("SELECT set_config('app.current_tenant_id', :t, true)"), {'t': str(tenant_id)})
        taken = bind.execute(sa.text("SELECT 1 FROM security_group WHERE tenant_id = :t AND name = :n"),
                             {'t': tenant_id, 'n': 'No access yet'}).first()
        bind.execute(sa.text(
            "INSERT INTO security_group (id, tenant_id, name, permissions, offered_to_customers, system_role) "
            "VALUES (:id, :t, :n, '[]', false, 'no_access')"),
            {'id': uuid.uuid4() if postgres else uuid.uuid4().hex, 't': tenant_id,
             'n': 'No access yet (built in)' if taken else 'No access yet'})


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        for (tenant_id,) in bind.execute(sa.text('SELECT id FROM tenant')).all():
            bind.execute(sa.text("SELECT set_config('app.current_tenant_id', :t, true)"), {'t': str(tenant_id)})
            bind.execute(sa.text("DELETE FROM security_group WHERE tenant_id = :t AND system_role = 'no_access' "
                                 "AND id NOT IN (SELECT security_group_id FROM tenant_membership)"), {'t': tenant_id})
    else:
        bind.execute(sa.text("DELETE FROM security_group WHERE system_role = 'no_access' "
                             "AND id NOT IN (SELECT security_group_id FROM tenant_membership)"))
    with op.batch_alter_table('security_group') as batch_op:
        batch_op.drop_column('system_role')
