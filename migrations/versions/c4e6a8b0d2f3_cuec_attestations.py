# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""CUEC mappings and attestations (CONTROLS_PLAN.md CT4)

Revision ID: c4e6a8b0d2f3
Revises: b3d5f7a9c1e2
Create Date: 2026-10-17 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4e6a8b0d2f3'
down_revision: Union[str, None] = 'b3d5f7a9c1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_NO_BANK = "NULLIF(current_setting('app.current_tenant_id', true), '') IS NULL"


def upgrade() -> None:
    op.create_table(
        'cuec_mapping',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=True),
        sa.Column('cuec_id', sa.String(length=30), nullable=False),
        sa.Column('control_ref', sa.String(length=100), nullable=False),
        sa.Column('control_name', sa.String(length=300), nullable=True),
        sa.Column('owner', sa.String(length=200), nullable=True),
        sa.Column('link', sa.String(length=1000), nullable=True),
        sa.Column('auto_accept', sa.Boolean(), nullable=False),
        sa.Column('inventory_version', sa.String(length=10), nullable=False),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_cuec_mapping_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_cuec_mapping_customer_id_customer')),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['user.id'], name=op.f('fk_cuec_mapping_created_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cuec_mapping')),
    )
    op.create_index(op.f('ix_cuec_mapping_tenant_id'), 'cuec_mapping', ['tenant_id'])
    op.create_index(op.f('ix_cuec_mapping_customer_id'), 'cuec_mapping', ['customer_id'])
    op.create_table(
        'cuec_attestation',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=True),
        sa.Column('customer_id', sa.Uuid(), nullable=True),
        sa.Column('cuec_id', sa.String(length=30), nullable=False),
        sa.Column('period_kind', sa.String(length=10), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('result', sa.String(length=30), nullable=False),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('statement', sa.Text(), nullable=True),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('files', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('prepared_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('prepared_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('accepted_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('auto_accepted', sa.Boolean(), nullable=False),
        sa.Column('rejected_reason', sa.Text(), nullable=True),
        sa.Column('rejected_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('inventory_version', sa.String(length=10), nullable=False),
        sa.Column('supersedes_id', sa.Uuid(), nullable=True),
        sa.Column('signed_body', sa.JSON(), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=True),
        sa.Column('signature', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_cuec_attestation_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_cuec_attestation_customer_id_customer')),
        sa.ForeignKeyConstraint(['prepared_by_user_id'], ['user.id'], name=op.f('fk_cuec_attestation_prepared_by_user_id_user')),
        sa.ForeignKeyConstraint(['accepted_by_user_id'], ['user.id'], name=op.f('fk_cuec_attestation_accepted_by_user_id_user')),
        sa.ForeignKeyConstraint(['rejected_by_user_id'], ['user.id'], name=op.f('fk_cuec_attestation_rejected_by_user_id_user')),
        sa.ForeignKeyConstraint(['supersedes_id'], ['cuec_attestation.id'], name=op.f('fk_cuec_attestation_supersedes_id_cuec_attestation')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cuec_attestation')),
    )
    op.create_index(op.f('ix_cuec_attestation_tenant_id'), 'cuec_attestation', ['tenant_id'])
    op.create_index(op.f('ix_cuec_attestation_customer_id'), 'cuec_attestation', ['customer_id'])
    op.create_index('ix_cuec_attestation_tenant_cuec_period', 'cuec_attestation', ['tenant_id', 'cuec_id', 'period_end'])
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('attestation_period', sa.String(length=10), server_default='quarterly', nullable=False))
        batch_op.add_column(sa.Column('attest_second_person', sa.Boolean(), server_default='0', nullable=False))

    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "cuec_mapping" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "cuec_mapping" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_cuec_mapping ON "cuec_mapping" USING ({_TENANT})')
        # The installation's own rows (host CUECs attested once by its platform staff) are
        # readable by every bank and written by no bank: only a session bound to none (the
        # platform's) can write them (CONTROLS_PLAN.md CT-D12).
        op.execute('ALTER TABLE "cuec_attestation" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "cuec_attestation" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_cuec_attestation ON "cuec_attestation" '
                   f'USING ({_TENANT} OR tenant_id IS NULL) WITH CHECK ({_TENANT} OR (tenant_id IS NULL AND {_NO_BANK}))')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_cuec_attestation ON "cuec_attestation"')
        op.execute('DROP POLICY IF EXISTS tenant_isolation_cuec_mapping ON "cuec_mapping"')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('attest_second_person')
        batch_op.drop_column('attestation_period')
    op.drop_index('ix_cuec_attestation_tenant_cuec_period', table_name='cuec_attestation')
    op.drop_index(op.f('ix_cuec_attestation_customer_id'), table_name='cuec_attestation')
    op.drop_index(op.f('ix_cuec_attestation_tenant_id'), table_name='cuec_attestation')
    op.drop_table('cuec_attestation')
    op.drop_index(op.f('ix_cuec_mapping_customer_id'), table_name='cuec_mapping')
    op.drop_index(op.f('ix_cuec_mapping_tenant_id'), table_name='cuec_mapping')
    op.drop_table('cuec_mapping')
