# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Issue-file layouts and upload corrections (MASTER_PLAN.md M5)

Revision ID: e2a4c6e8a0b3
Revises: d1f3b5d7f9a2
Create Date: 2026-10-11 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2a4c6e8a0b3'
down_revision: Union[str, None] = 'd1f3b5d7f9a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'import_layout',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=True),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('settings', sa.JSON(), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_import_layout_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_import_layout_customer_id_customer')),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['user.id'], name=op.f('fk_import_layout_created_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_import_layout')),
    )
    op.create_index(op.f('ix_import_layout_tenant_id'), 'import_layout', ['tenant_id'])
    op.create_index(op.f('ix_import_layout_customer_id'), 'import_layout', ['customer_id'])

    op.create_table(
        'bulk_upload_correction',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('bulk_upload_file_id', sa.Uuid(), nullable=False),
        sa.Column('row_index', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('created_id', sa.Uuid(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('error_field', sa.String(length=60), nullable=True),
        sa.Column('corrected_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('corrected_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_bulk_upload_correction_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['bulk_upload_file_id'], ['bulk_upload_file.id'],
                                name=op.f('fk_bulk_upload_correction_bulk_upload_file_id_bulk_upload_file')),
        sa.ForeignKeyConstraint(['corrected_by_user_id'], ['user.id'], name=op.f('fk_bulk_upload_correction_corrected_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_bulk_upload_correction')),
    )
    op.create_index(op.f('ix_bulk_upload_correction_tenant_id'), 'bulk_upload_correction', ['tenant_id'])
    op.create_index(op.f('ix_bulk_upload_correction_bulk_upload_file_id'), 'bulk_upload_correction', ['bulk_upload_file_id'])

    with op.batch_alter_table('bulk_upload_row_result') as batch_op:
        batch_op.add_column(sa.Column('error_field', sa.String(length=60), nullable=True))

    if op.get_bind().dialect.name == 'postgresql':
        for table in ('import_layout', 'bulk_upload_correction'):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        for table in ('import_layout', 'bulk_upload_correction'):
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
    with op.batch_alter_table('bulk_upload_row_result') as batch_op:
        batch_op.drop_column('error_field')
    op.drop_index(op.f('ix_bulk_upload_correction_bulk_upload_file_id'), table_name='bulk_upload_correction')
    op.drop_index(op.f('ix_bulk_upload_correction_tenant_id'), table_name='bulk_upload_correction')
    op.drop_table('bulk_upload_correction')
    op.drop_index(op.f('ix_import_layout_customer_id'), table_name='import_layout')
    op.drop_index(op.f('ix_import_layout_tenant_id'), table_name='import_layout')
    op.drop_table('import_layout')
