# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""job: a durable background-job queue (SCALE_PLAN.md S3)

Revision ID: c4f6a8b0d2e5
Revises: b2e4f6a8c0d3
Create Date: 2026-09-28 10:00:00.000000

No RLS policy on purpose: workers claim every bank's jobs, then bind to the job's bank.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4f6a8b0d2e5'
down_revision: Union[str, None] = 'b2e4f6a8c0d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'job',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('dedupe_key', sa.String(length=200), nullable=True),
        sa.Column('status', sa.Enum('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', name='job_status', native_enum=False, length=20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('run_after', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('locked_by', sa.String(length=200), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_job_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_job')),
    )
    op.create_index('ix_job_status_run_after', 'job', ['status', 'run_after'])
    op.create_index('ix_job_dedupe_key_status', 'job', ['dedupe_key', 'status'])
    op.create_index(op.f('ix_job_tenant_id'), 'job', ['tenant_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_job_tenant_id'), table_name='job')
    op.drop_index('ix_job_dedupe_key_status', table_name='job')
    op.drop_index('ix_job_status_run_after', table_name='job')
    op.drop_table('job')
