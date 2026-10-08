# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""bulk uploads imported by a background job: status and progress on bulk_upload_file,
and bulk_upload_row_result (SCALE_PLAN.md S4)

Revision ID: a8c0e2f4b6d9
Revises: f7c9e1a3b5d6
Create Date: 2026-10-08 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a8c0e2f4b6d9'
down_revision: Union[str, None] = 'f7c9e1a3b5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    with op.batch_alter_table('bulk_upload_file') as batch_op:
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=False, server_default='RECEIVED'))
        batch_op.add_column(sa.Column('total_rows', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('processed_rows', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('options', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('exception_counts', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    # Files from before this release were processed inside the request: done if they got
    # result counts, otherwise rejected.
    op.execute("UPDATE bulk_upload_file SET status = 'COMPLETED' WHERE succeeded_count IS NOT NULL")
    op.execute("UPDATE bulk_upload_file SET status = 'REJECTED' WHERE succeeded_count IS NULL")

    op.create_table(
        'bulk_upload_row_result',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('bulk_upload_file_id', sa.Uuid(), nullable=False),
        sa.Column('row_index', sa.Integer(), nullable=False),
        sa.Column('row_label', sa.String(length=200), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('created_id', sa.Uuid(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_bulk_upload_row_result_tenant_id_tenant')),
        sa.ForeignKeyConstraint(
            ['bulk_upload_file_id'], ['bulk_upload_file.id'], name=op.f('fk_bulk_upload_row_result_bulk_upload_file_id_bulk_upload_file')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_bulk_upload_row_result')),
        sa.UniqueConstraint('bulk_upload_file_id', 'row_index', name=op.f('uq_bulk_upload_row_result_bulk_upload_file_id')),
    )
    op.create_index(op.f('ix_bulk_upload_row_result_tenant_id'), 'bulk_upload_row_result', ['tenant_id'])

    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "bulk_upload_row_result" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "bulk_upload_row_result" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_bulk_upload_row_result ON "bulk_upload_row_result" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_bulk_upload_row_result ON "bulk_upload_row_result"')
    op.drop_index(op.f('ix_bulk_upload_row_result_tenant_id'), table_name='bulk_upload_row_result')
    op.drop_table('bulk_upload_row_result')
    with op.batch_alter_table('bulk_upload_file') as batch_op:
        for column in ('completed_at', 'started_at', 'error', 'exception_counts', 'options', 'processed_rows', 'total_rows', 'status'):
            batch_op.drop_column(column)
