# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""security: widen signature columns for ECDSA key-pair signing, rename
bulk_upload_file.hmac_signature_hex to signature_hex

Revision ID: b4e1c9a0d752
Revises: a7d4f0e29c31
Create Date: 2026-08-07 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4e1c9a0d752'
down_revision: Union[str, None] = 'a7d4f0e29c31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # HMAC-SHA256/plain hash values were String(64) (a 64-hex-char digest); an ECDSA
    # P-256 DER signature hex-encodes to up to ~144 chars, so these need to widen
    # regardless of the rename below. Existing rows (all local dev/test data — see the
    # plan's note on openclear.db's row counts) won't re-verify under the new keys; that's
    # an accepted one-time cutover cost for a pre-launch platform, not a migration bug.
    with op.batch_alter_table('bulk_upload_file') as batch_op:
        batch_op.alter_column('hmac_signature_hex', new_column_name='signature_hex', type_=sa.String(200), existing_nullable=False)

    with op.batch_alter_table('audit_log_entry') as batch_op:
        batch_op.alter_column('entry_hash', type_=sa.String(200), existing_nullable=False)
        batch_op.alter_column('prev_entry_hash', type_=sa.String(200), existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('audit_log_entry') as batch_op:
        batch_op.alter_column('prev_entry_hash', type_=sa.String(64), existing_nullable=True)
        batch_op.alter_column('entry_hash', type_=sa.String(64), existing_nullable=False)

    with op.batch_alter_table('bulk_upload_file') as batch_op:
        batch_op.alter_column('signature_hex', new_column_name='hmac_signature_hex', type_=sa.String(64), existing_nullable=False)
