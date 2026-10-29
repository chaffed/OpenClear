# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom-field updates by file and API (plans/CUSTOM_FIELDS_PLAN.md CF5): each row result keeps
the values before and after; existing Admin groups get custom_field:write

Revision ID: d6f2a8c4e1b9
Revises: c4d2e8f1a7b3
Create Date: 2026-10-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd6f2a8c4e1b9'
down_revision: Union[str, None] = 'c4d2e8f1a7b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSION = 'custom_field:write'


def upgrade() -> None:
    with op.batch_alter_table('bulk_upload_row_result') as batch_op:
        batch_op.add_column(sa.Column('changes', sa.JSON(), nullable=True))

    # Existing "Admin" groups get the new permission (new banks' Admin gets it from
    # auth/permissions.py).
    bind = op.get_bind()
    groups = sa.table('security_group', sa.column('id', sa.Uuid()), sa.column('name', sa.String()), sa.column('permissions', sa.JSON()))
    for group_id, permissions in bind.execute(sa.select(groups.c.id, groups.c.permissions).where(groups.c.name == 'Admin')).all():
        if _PERMISSION not in (permissions or []):
            bind.execute(sa.update(groups).where(groups.c.id == group_id).values(permissions=[*(permissions or []), _PERMISSION]))


def downgrade() -> None:
    bind = op.get_bind()
    groups = sa.table('security_group', sa.column('id', sa.Uuid()), sa.column('permissions', sa.JSON()))
    for group_id, permissions in bind.execute(sa.select(groups.c.id, groups.c.permissions)).all():
        if permissions and _PERMISSION in permissions:
            bind.execute(sa.update(groups).where(groups.c.id == group_id).values(permissions=[p for p in permissions if p != _PERMISSION]))
    with op.batch_alter_table('bulk_upload_row_result') as batch_op:
        batch_op.drop_column('changes')
