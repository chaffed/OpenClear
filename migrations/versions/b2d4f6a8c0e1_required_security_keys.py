# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Required security keys, platform staff keys, the SSO MFA check, key-pair-only API clients
(OWASP_PLAN.md OW3b, 1.23.0)

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5f7b9d2
Create Date: 2026-10-18 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2d4f6a8c0e1'
down_revision: Union[str, None] = 'a1c3e5f7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('require_security_key', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('security_key_grace_days', sa.Integer(), nullable=False, server_default='14'))
        batch_op.add_column(sa.Column('security_key_required_since', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('api_clients_key_pair_only', sa.Boolean(), nullable=False, server_default='0'))
    with op.batch_alter_table('customer') as batch_op:
        batch_op.add_column(sa.Column('require_security_key', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('security_key_required_since', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('sso_connection') as batch_op:
        batch_op.add_column(sa.Column('require_mfa_claim', sa.Boolean(), nullable=False, server_default='0'))
    # Platform staff's keys and challenges belong to no bank (ADR 0005): no row-level security.
    op.create_table(
        'platform_webauthn_credential',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('credential_id', sa.LargeBinary(), nullable=False),
        sa.Column('public_key', sa.LargeBinary(), nullable=False),
        sa.Column('sign_count', sa.Integer(), nullable=False),
        sa.Column('transports', sa.JSON(), nullable=True),
        sa.Column('nickname', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_platform_webauthn_credential_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_platform_webauthn_credential')),
        sa.UniqueConstraint('credential_id', name=op.f('uq_platform_webauthn_credential_credential_id')),
    )
    op.create_index(op.f('ix_platform_webauthn_credential_user_id'), 'platform_webauthn_credential', ['user_id'])
    op.create_table(
        'platform_webauthn_challenge',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('challenge', sa.LargeBinary(), nullable=False),
        sa.Column('purpose', sa.String(length=20), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_platform_webauthn_challenge_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_platform_webauthn_challenge')),
    )
    op.create_index(op.f('ix_platform_webauthn_challenge_user_id'), 'platform_webauthn_challenge', ['user_id'])


def downgrade() -> None:
    op.drop_table('platform_webauthn_challenge')
    op.drop_table('platform_webauthn_credential')
    with op.batch_alter_table('sso_connection') as batch_op:
        batch_op.drop_column('require_mfa_claim')
    with op.batch_alter_table('customer') as batch_op:
        batch_op.drop_column('security_key_required_since')
        batch_op.drop_column('require_security_key')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('api_clients_key_pair_only')
        batch_op.drop_column('security_key_required_since')
        batch_op.drop_column('security_key_grace_days')
        batch_op.drop_column('require_security_key')
