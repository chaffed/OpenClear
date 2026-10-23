# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Incident contacts and notices (REGULATORY_PLAN.md RG3; 12 CFR 304.24, 23 NYCRR 500.11(b)(3))

incident_contact (a bank's designated contacts, and its CEO and CIO as the fallback) and
incident_notice_delivery (each bank's copy of a notice) have row-level security; incident_notice
belongs to the installation. tenant.incident_contacts_confirmed_at records the yearly confirmation.

Revision ID: d867a5f28e73
Revises: 44ed7f9be66a
Create Date: 2026-10-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd867a5f28e73'
down_revision: Union[str, None] = '44ed7f9be66a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        'incident_contact',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=255), nullable=True),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('phone', sa.String(length=40), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_incident_contact_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_incident_contact')),
    )
    op.create_index(op.f('ix_incident_contact_tenant_id'), 'incident_contact', ['tenant_id'], unique=False)
    op.create_table(
        'incident_notice',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(length=15), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('determined_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('services_affected', sa.String(length=500), nullable=True),
        sa.Column('bank_data_affected', sa.Boolean(), nullable=False),
        sa.Column('follows_id', sa.Uuid(), nullable=True),
        sa.Column('issued_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['follows_id'], ['incident_notice.id'], name=op.f('fk_incident_notice_follows_id_incident_notice')),
        sa.ForeignKeyConstraint(['issued_by_user_id'], ['user.id'], name=op.f('fk_incident_notice_issued_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_incident_notice')),
    )
    op.create_table(
        'incident_notice_delivery',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('notice_id', sa.Uuid(), nullable=False),
        sa.Column('recipients', sa.JSON(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_by_user_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_incident_notice_delivery_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['notice_id'], ['incident_notice.id'], name=op.f('fk_incident_notice_delivery_notice_id_incident_notice')),
        sa.ForeignKeyConstraint(['acknowledged_by_user_id'], ['user.id'],
                                name=op.f('fk_incident_notice_delivery_acknowledged_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_incident_notice_delivery')),
        sa.UniqueConstraint('notice_id', 'tenant_id', name='uq_incident_notice_delivery'),
    )
    op.create_index(op.f('ix_incident_notice_delivery_tenant_id'), 'incident_notice_delivery', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_incident_notice_delivery_notice_id'), 'incident_notice_delivery', ['notice_id'], unique=False)
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('incident_contacts_confirmed_at', sa.DateTime(timezone=True), nullable=True))
    if op.get_bind().dialect.name == 'postgresql':
        for table in ('incident_contact', 'incident_notice_delivery'):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')


def downgrade() -> None:
    with op.batch_alter_table('tenant') as batch_op:
        batch_op.drop_column('incident_contacts_confirmed_at')
    op.drop_index(op.f('ix_incident_notice_delivery_notice_id'), table_name='incident_notice_delivery')
    op.drop_index(op.f('ix_incident_notice_delivery_tenant_id'), table_name='incident_notice_delivery')
    op.drop_table('incident_notice_delivery')
    op.drop_table('incident_notice')
    op.drop_index(op.f('ix_incident_contact_tenant_id'), table_name='incident_contact')
    op.drop_table('incident_contact')
