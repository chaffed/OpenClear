# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Online banking sign-on profile: sso_connection.customer_claim, api_client.sso_connection_id (SDK_PLAN.md phase F)

Revision ID: e0a2c4e6a8b1
Revises: d9f1b3c5e7a9
Create Date: 2026-10-09 00:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e0a2c4e6a8b1'
down_revision: Union[str, None] = 'd9f1b3c5e7a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('sso_connection') as batch_op:
        batch_op.add_column(sa.Column('customer_claim', sa.String(length=100), nullable=True))
    with op.batch_alter_table('api_client') as batch_op:
        batch_op.add_column(sa.Column('sso_connection_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(op.f('fk_api_client_sso_connection_id_sso_connection'), 'sso_connection', ['sso_connection_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('api_client') as batch_op:
        batch_op.drop_constraint(op.f('fk_api_client_sso_connection_id_sso_connection'), type_='foreignkey')
        batch_op.drop_column('sso_connection_id')
    with op.batch_alter_table('sso_connection') as batch_op:
        batch_op.drop_column('customer_claim')
