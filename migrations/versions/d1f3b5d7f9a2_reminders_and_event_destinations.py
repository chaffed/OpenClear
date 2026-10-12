# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Reminders before the cutoff, and security event destinations (MASTER_PLAN.md M4)

Revision ID: d1f3b5d7f9a2
Revises: c0e2a4c6e8f1
Create Date: 2026-10-11 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1f3b5d7f9a2'
down_revision: Union[str, None] = 'c0e2a4c6e8f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('reminder_offsets_minutes', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('security_event_feed_read_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('customer') as batch_op:
        batch_op.add_column(sa.Column('reminder_offsets_minutes', sa.String(length=64), nullable=True))

    op.create_table(
        'deadline_reminder',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('exception_item_id', sa.Uuid(), nullable=False),
        sa.Column('offset_minutes', sa.Integer(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_deadline_reminder_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['exception_item_id'], ['exception_item.id'],
                                name=op.f('fk_deadline_reminder_exception_item_id_exception_item')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_deadline_reminder')),
        sa.UniqueConstraint('exception_item_id', 'offset_minutes', name=op.f('uq_deadline_reminder_exception_item_id')),
    )
    op.create_index(op.f('ix_deadline_reminder_tenant_id'), 'deadline_reminder', ['tenant_id'])

    op.create_table(
        'security_event_destination',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=True),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('host', sa.String(length=255), nullable=True),
        sa.Column('port', sa.Integer(), nullable=True),
        sa.Column('transport', sa.String(length=10), nullable=True),
        sa.Column('format', sa.String(length=10), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=True),
        sa.Column('token_encrypted', sa.Text(), nullable=True),
        sa.Column('splunk_index', sa.String(length=80), nullable=True),
        sa.Column('splunk_sourcetype', sa.String(length=80), nullable=True),
        sa.Column('cursor', sa.BigInteger(), nullable=False),
        sa.Column('delivered_count', sa.BigInteger(), nullable=False),
        sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=1000), nullable=True),
        sa.Column('failing_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('consecutive_failures', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_security_event_destination_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_security_event_destination')),
        sa.UniqueConstraint('tenant_id', name=op.f('uq_security_event_destination_tenant_id')),
    )

    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "deadline_reminder" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "deadline_reminder" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_deadline_reminder ON "deadline_reminder" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_deadline_reminder ON "deadline_reminder"')
    op.drop_table('security_event_destination')
    op.drop_index(op.f('ix_deadline_reminder_tenant_id'), table_name='deadline_reminder')
    op.drop_table('deadline_reminder')
    with op.batch_alter_table('customer') as batch_op:
        batch_op.drop_column('reminder_offsets_minutes')
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('security_event_feed_read_at')
        batch_op.drop_column('reminder_offsets_minutes')
