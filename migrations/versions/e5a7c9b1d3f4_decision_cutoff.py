# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Decision cut-offs and decisions changeable until them (UI_PLAN.md UI-D13)

The bank's time zone, cut-off per network and minimum review time; a customer's negotiated
cut-off; the bank's closures; when each decision locked; and the earlier versions of a decision
changed before its cut-off. Every decision made before this locked when it was made.

Revision ID: e5a7c9b1d3f4
Revises: d4f6b8c0e2a3
Create Date: 2026-10-20 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5a7c9b1d3f4'
down_revision: Union[str, None] = 'd4f6b8c0e2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def _protect(table: str) -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('time_zone', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('decision_cutoffs', sa.JSON(), nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('cutoff_min_review_minutes', sa.Integer(), nullable=False, server_default='120'))
    with op.batch_alter_table('customer_disposition_setting') as batch_op:
        batch_op.add_column(sa.Column('cutoff_time', sa.String(length=5), nullable=True))
    with op.batch_alter_table('decision') as batch_op:
        batch_op.add_column(sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True))
    # Every decision so far was final when it was made.
    op.execute('UPDATE decision SET locked_at = decided_at')

    op.create_table(
        'bank_closure',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('closed_on', sa.Date(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_bank_closure_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_closure')),
        sa.UniqueConstraint('tenant_id', 'closed_on', name='uq_bank_closure_tenant_day'),
    )
    op.create_index(op.f('ix_bank_closure_tenant_id'), 'bank_closure', ['tenant_id'])
    _protect('bank_closure')

    op.create_table(
        'decision_revision',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('exception_item_id', sa.Uuid(), nullable=False),
        sa.Column('outcome', sa.Enum('PAY', 'RETURN', name='decision_outcome', native_enum=False, length=10), nullable=False),
        sa.Column('reason_code', sa.String(length=64), nullable=False),
        sa.Column('return_transaction_code', sa.String(length=10), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('submitted_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('decided_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revised_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('revised_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('undone', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_decision_revision_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['exception_item_id'], ['exception_item.id'], name=op.f('fk_decision_revision_exception_item_id_exception_item')),
        sa.ForeignKeyConstraint(['submitted_by_user_id'], ['user.id'], name=op.f('fk_decision_revision_submitted_by_user_id_user')),
        sa.ForeignKeyConstraint(['decided_by_user_id'], ['user.id'], name=op.f('fk_decision_revision_decided_by_user_id_user')),
        sa.ForeignKeyConstraint(['revised_by_user_id'], ['user.id'], name=op.f('fk_decision_revision_revised_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_decision_revision')),
    )
    op.create_index(op.f('ix_decision_revision_tenant_id'), 'decision_revision', ['tenant_id'])
    op.create_index(op.f('ix_decision_revision_exception_item_id'), 'decision_revision', ['exception_item_id'])
    _protect('decision_revision')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_decision_revision ON "decision_revision"')
        op.execute('DROP POLICY IF EXISTS tenant_isolation_bank_closure ON "bank_closure"')
    op.drop_table('decision_revision')
    op.drop_table('bank_closure')
    with op.batch_alter_table('decision') as batch_op:
        batch_op.drop_column('locked_at')
    with op.batch_alter_table('customer_disposition_setting') as batch_op:
        batch_op.drop_column('cutoff_time')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('cutoff_min_review_minutes')
        batch_op.drop_column('decision_cutoffs')
        batch_op.drop_column('time_zone')
