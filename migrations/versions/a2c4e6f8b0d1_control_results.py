# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Control test results, deviations and scheduled job runs (CONTROLS_PLAN.md CT2)

Revision ID: a2c4e6f8b0d1
Revises: f1a3c5e7b9d2
Create Date: 2026-10-16 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a2c4e6f8b0d1'
down_revision: Union[str, None] = 'f1a3c5e7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_PROTECTED = ('control_test_result', 'control_deviation')


def upgrade() -> None:
    op.create_table(
        'control_test_result',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('check_key', sa.String(length=60), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('evidence_sha256', sa.String(length=64), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_by', sa.String(length=10), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_control_test_result_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_control_test_result_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_control_test_result')),
    )
    op.create_index(op.f('ix_control_test_result_tenant_id'), 'control_test_result', ['tenant_id'])
    op.create_index('ix_control_test_result_tenant_check_recorded', 'control_test_result', ['tenant_id', 'check_key', 'recorded_at'])
    op.create_table(
        'control_deviation',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('check_key', sa.String(length=60), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('opened_result_id', sa.Uuid(), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_result_id', sa.Uuid(), nullable=True),
        sa.Column('cause', sa.Text(), nullable=True),
        sa.Column('response', sa.Text(), nullable=True),
        sa.Column('responded_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_control_deviation_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['opened_result_id'], ['control_test_result.id'], name=op.f('fk_control_deviation_opened_result_id_control_test_result')),
        sa.ForeignKeyConstraint(['closed_result_id'], ['control_test_result.id'], name=op.f('fk_control_deviation_closed_result_id_control_test_result')),
        sa.ForeignKeyConstraint(['responded_by_user_id'], ['user.id'], name=op.f('fk_control_deviation_responded_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_control_deviation')),
    )
    op.create_index(op.f('ix_control_deviation_tenant_id'), 'control_deviation', ['tenant_id'])
    op.create_index('ix_control_deviation_tenant_check_opened', 'control_deviation', ['tenant_id', 'check_key', 'opened_at'])
    op.create_table(
        'scheduled_job_run',
        sa.Column('job_id', sa.String(length=80), nullable=False),
        sa.Column('last_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_ok', sa.Boolean(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('job_id', name=op.f('pk_scheduled_job_run')),
    )
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('control_grace_hours', sa.Integer(), server_default='24', nullable=False))
        batch_op.add_column(sa.Column('control_warn_deviations', sa.JSON(), server_default='[]', nullable=False))

    if op.get_bind().dialect.name == 'postgresql':
        for table in _PROTECTED:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        for table in _PROTECTED:
            op.execute(f'DROP POLICY IF EXISTS tenant_isolation_{table} ON "{table}"')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('control_warn_deviations')
        batch_op.drop_column('control_grace_hours')
    op.drop_table('scheduled_job_run')
    op.drop_index('ix_control_deviation_tenant_check_opened', table_name='control_deviation')
    op.drop_index(op.f('ix_control_deviation_tenant_id'), table_name='control_deviation')
    op.drop_table('control_deviation')
    op.drop_index('ix_control_test_result_tenant_check_recorded', table_name='control_test_result')
    op.drop_index(op.f('ix_control_test_result_tenant_id'), table_name='control_test_result')
    op.drop_table('control_test_result')
