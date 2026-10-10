# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Control test runs: replay and anomaly capture (COMPLIANCE_PLAN.md C2)

The same release moves stored secrets from Fernet to AES-256-GCM (auth/crypto.py), at
application startup rather than here. Downgrading converts them back to Fernet, so the
previous version can still read them.

Revision ID: a8c0e2a4c6e9
Revises: f7b9d1f3b5d8
Create Date: 2026-10-10 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a8c0e2a4c6e9'
down_revision: Union[str, None] = 'f7b9d1f3b5d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'control_run',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('passed', sa.Boolean(), nullable=True),
        sa.Column('summary', sa.String(length=500), nullable=False),
        sa.Column('report', sa.JSON(), nullable=False),
        sa.Column('requested_by_user_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_control_run_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['requested_by_user_id'], ['user.id'], name=op.f('fk_control_run_requested_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_control_run')),
    )
    op.create_index('ix_control_run_tenant_kind_started', 'control_run', ['tenant_id', 'kind', 'started_at'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "control_run" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "control_run" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_control_run ON "control_run" USING ({_TENANT})')


def _secrets_back_to_fernet() -> None:
    from openclear.auth import crypto

    bind = op.get_bind()
    postgres = bind.dialect.name == 'postgresql'
    tenants = bind.execute(sa.text('SELECT id FROM tenant')).scalars().all()
    for table, column in crypto.ENCRYPTED_COLUMNS:
        for tenant_id in tenants:
            if postgres:  # row-level security is forced on these tables
                bind.execute(sa.text("SELECT set_config('app.current_tenant_id', :t, true)"), {'t': str(tenant_id)})
            rows = bind.execute(sa.text(f'SELECT id, "{column}" FROM "{table}" WHERE tenant_id = :t AND "{column}" LIKE :v2'),
                                {'t': tenant_id, 'v2': 'v2:%'}).all()
            for row_id, value in rows:
                bind.execute(sa.text(f'UPDATE "{table}" SET "{column}" = :value WHERE id = :id'),
                             {'value': crypto.legacy_encrypt(crypto.decrypt_secret(value)), 'id': row_id})


def downgrade() -> None:
    _secrets_back_to_fernet()
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_control_run ON "control_run"')
    op.drop_table('control_run')
