# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Replacing a SAML connection's signing key (issue #21): the next certificate and key, and when
the change started

Revision ID: e7a3b9d5c2f1
Revises: d6f2a8c4e1b9
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7a3b9d5c2f1'
down_revision: Union[str, None] = 'd6f2a8c4e1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('sso_connection') as batch_op:
        batch_op.add_column(sa.Column('saml_sp_next_certificate', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('saml_sp_next_private_key_encrypted', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('saml_sp_rekey_started_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('sso_connection') as batch_op:
        batch_op.drop_column('saml_sp_rekey_started_at')
        batch_op.drop_column('saml_sp_next_private_key_encrypted')
        batch_op.drop_column('saml_sp_next_certificate')
