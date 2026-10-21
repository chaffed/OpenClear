# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank's own Written Statement of Unauthorized Debit form, instead of signing one in OpenClear
(SIMPLIFY_PLAN.md SP1)

Adds bank_form (with row-level security), drops wsud_statement and wsud_statement_transaction, and
takes the wsud:sign and wsud:read permissions out of every security group. There are no
installations with statements to keep; the downgrade recreates the tables empty.

Revision ID: 3971bd3709a0
Revises: 07203f56ed30
Create Date: 2026-10-21
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3971bd3709a0'
down_revision: Union[str, None] = '07203f56ed30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_REMOVED = {"wsud:sign", "wsud:read"}


def upgrade() -> None:
    op.create_table(
        'bank_form',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('format', sa.String(length=10), nullable=False),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('storage_ref', sa.String(length=1000), nullable=True),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('updated_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_bank_form_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['user.id'], name=op.f('fk_bank_form_updated_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_form')),
        sa.UniqueConstraint('tenant_id', 'key', name='uq_bank_form_tenant_key'),
    )
    op.create_index(op.f('ix_bank_form_tenant_id'), 'bank_form', ['tenant_id'], unique=False)
    bind = op.get_bind()
    postgres = bind.dialect.name == 'postgresql'
    if postgres:
        op.execute('ALTER TABLE "bank_form" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "bank_form" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_bank_form ON "bank_form" USING ({_TENANT})')

    op.drop_table('wsud_statement_transaction')
    op.drop_table('wsud_statement')

    for (tenant_id,) in bind.execute(sa.text('SELECT id FROM tenant')).all():
        if postgres:  # row-level security is forced on security_group
            bind.execute(sa.text("SELECT set_config('app.current_tenant_id', :t, true)"), {'t': str(tenant_id)})
        for group_id, permissions in bind.execute(sa.text('SELECT id, permissions FROM security_group WHERE tenant_id = :t'),
                                                  {'t': tenant_id}).all():
            held = json.loads(permissions) if isinstance(permissions, str) else list(permissions or [])
            kept = [p for p in held if p not in _REMOVED]
            if kept != held:
                bind.execute(sa.text('UPDATE security_group SET permissions = :p WHERE id = :id'),
                             {'p': json.dumps(kept), 'id': group_id})


def downgrade() -> None:
    op.create_table(
        'wsud_statement',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=False),
        sa.Column('signed_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('signer_typed_name', sa.String(length=255), nullable=False),
        sa.Column('signer_ip_address', sa.String(length=64), nullable=True),
        sa.Column('signer_user_agent', sa.String(length=500), nullable=True),
        sa.Column('consent_disclosure_version', sa.String(length=20), nullable=False),
        sa.Column('statement_text_snapshot', sa.Text(), nullable=False),
        sa.Column('signed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('signature_hex', sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_wsud_statement_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_wsud_statement_customer_id_customer')),
        sa.ForeignKeyConstraint(['signed_by_user_id'], ['user.id'], name=op.f('fk_wsud_statement_signed_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_wsud_statement')),
    )
    op.create_index(op.f('ix_wsud_statement_tenant_id'), 'wsud_statement', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_wsud_statement_customer_id'), 'wsud_statement', ['customer_id'], unique=False)
    op.create_table(
        'wsud_statement_transaction',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('wsud_statement_id', sa.Uuid(), nullable=False),
        sa.Column('ach_transaction_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_wsud_statement_transaction_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['wsud_statement_id'], ['wsud_statement.id'],
                                name=op.f('fk_wsud_statement_transaction_wsud_statement_id_wsud_statement')),
        sa.ForeignKeyConstraint(['ach_transaction_id'], ['ach_transaction.id'],
                                name=op.f('fk_wsud_statement_transaction_ach_transaction_id_ach_transaction')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_wsud_statement_transaction')),
        sa.UniqueConstraint('wsud_statement_id', 'ach_transaction_id', name='uq_wsud_statement_transaction'),
    )
    op.create_index(op.f('ix_wsud_statement_transaction_tenant_id'), 'wsud_statement_transaction', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_wsud_statement_transaction_wsud_statement_id'), 'wsud_statement_transaction', ['wsud_statement_id'],
                    unique=False)
    op.create_index(op.f('ix_wsud_statement_transaction_ach_transaction_id'), 'wsud_statement_transaction', ['ach_transaction_id'],
                    unique=False)
    if op.get_bind().dialect.name == 'postgresql':
        for table in ('wsud_statement', 'wsud_statement_transaction'):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation_{table} ON "{table}" USING ({_TENANT})')
        op.execute('DROP POLICY IF EXISTS tenant_isolation_bank_form ON "bank_form"')
    op.drop_index(op.f('ix_bank_form_tenant_id'), table_name='bank_form')
    op.drop_table('bank_form')
