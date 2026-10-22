# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""An advisory fraud score from the bank's own model, off until the bank turns it on
(SIMPLIFY_PLAN.md SP2)

- tenant: adds ml_scoring_enabled (off); drops the shared-or-bank-only choice and its consent.
- ml_model: every model belongs to one bank. Shared models (no bank) and customer models are
  deleted, customer_id goes, tenant_id is required, and the table gets row-level security.
- customer_ml_setting goes. A default decision of "ml_determined" becomes "none", and a decision
  the model made at a cut-off is recorded as a default ("auto_default").
- Fraud-training examples go: their exceptions, decisions, synthetic items and upload files,
  exception_item's training columns, and the ml_training_example:write permission.
- The platform key scope "shared_model" goes, and pending requests for the removed changes are
  withdrawn.

There are no installations with data to keep; the downgrade recreates the columns and the table
empty. Model artifact files of deleted rows are left on disk.

Revision ID: 5b8e2d4f6a1c
Revises: 3971bd3709a0
Create Date: 2026-10-21
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5b8e2d4f6a1c'
down_revision: Union[str, None] = '3971bd3709a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_REMOVED_PERMISSION = "ml_training_example:write"
_REMOVED_KINDS = ("customer.ml_mode", "ml.switch_to_shared", "platform.shared_model.activate")
_TRAINING_EXCEPTIONS = "SELECT id FROM exception_item WHERE source = 'TRAINING_BACKFILL' AND tenant_id = :t"
_TRAINING_UPLOADS = "SELECT id FROM bulk_upload_file WHERE kind = 'FRAUD_TRAINING_EXAMPLES' AND tenant_id = :t"


def _each_bank(bind, postgres: bool):
    for (tenant_id,) in bind.execute(sa.text('SELECT id FROM tenant')).all():
        if postgres:  # row-level security is forced on most of the tables below
            bind.execute(sa.text("SELECT set_config('app.current_tenant_id', :t, true)"), {'t': str(tenant_id)})
        yield tenant_id


def upgrade() -> None:
    bind = op.get_bind()
    postgres = bind.dialect.name == 'postgresql'

    for tenant_id in _each_bank(bind, postgres):
        t = {'t': tenant_id}
        for group_id, permissions in bind.execute(sa.text('SELECT id, permissions FROM security_group WHERE tenant_id = :t'), t).all():
            held = json.loads(permissions) if isinstance(permissions, str) else list(permissions or [])
            kept = [p for p in held if p != _REMOVED_PERMISSION]
            if kept != held:
                bind.execute(sa.text('UPDATE security_group SET permissions = :p WHERE id = :id'), {'p': json.dumps(kept), 'id': group_id})
        bind.execute(sa.text("UPDATE customer_disposition_setting SET mode = 'NONE' WHERE mode = 'ML_DETERMINED' AND tenant_id = :t"), t)
        bind.execute(sa.text("UPDATE change_request SET status = 'withdrawn' WHERE status = 'pending' AND tenant_id = :t "
                             "AND kind IN :kinds").bindparams(sa.bindparam('kinds', expanding=True)),
                     {**t, 'kinds': list(_REMOVED_KINDS)})
        for table in ('decision_revision', 'deadline_reminder', 'decision'):
            bind.execute(sa.text(f'DELETE FROM {table} WHERE exception_item_id IN ({_TRAINING_EXCEPTIONS})'), t)
        bind.execute(sa.text(f'DELETE FROM exception_item WHERE id IN ({_TRAINING_EXCEPTIONS})'), t)
        bind.execute(sa.text("UPDATE check_image SET paid_item_id = NULL WHERE paid_item_id IN "
                             "(SELECT id FROM paid_item WHERE source = 'TRAINING_BACKFILL' AND tenant_id = :t)"), t)
        bind.execute(sa.text("DELETE FROM paid_item WHERE source = 'TRAINING_BACKFILL' AND tenant_id = :t"), t)
        bind.execute(sa.text("DELETE FROM ach_transaction WHERE source = 'TRAINING_BACKFILL' AND tenant_id = :t"), t)
        for table in ('bulk_upload_row_result', 'bulk_upload_correction', 'bulk_upload_created_record'):
            bind.execute(sa.text(f'DELETE FROM {table} WHERE bulk_upload_file_id IN ({_TRAINING_UPLOADS})'), t)
        bind.execute(sa.text(f'DELETE FROM bulk_upload_file WHERE id IN ({_TRAINING_UPLOADS})'), t)

    # The service provider's own requests (no bank) and the keys belong to no bank.
    bind.execute(sa.text("UPDATE change_request SET status = 'withdrawn' WHERE status = 'pending' AND tenant_id IS NULL "
                         "AND kind IN :kinds").bindparams(sa.bindparam('kinds', expanding=True)), {'kinds': list(_REMOVED_KINDS)})
    for key_id, scopes in bind.execute(sa.text('SELECT id, scopes FROM platform_api_key')).all():
        held = json.loads(scopes) if isinstance(scopes, str) else list(scopes or [])
        kept = [s for s in held if s != 'shared_model'] or ['usage']
        if kept != held:
            bind.execute(sa.text('UPDATE platform_api_key SET scopes = :s WHERE id = :id'), {'s': json.dumps(kept), 'id': key_id})

    # decision has no row-level security (tests/test_postgres/test_rls_plugins.py).
    op.execute("UPDATE decision SET source = 'AUTO_DEFAULT' WHERE source = 'AUTO_ML'")
    op.execute('DELETE FROM ml_model WHERE customer_id IS NOT NULL OR tenant_id IS NULL')
    with op.batch_alter_table('ml_model') as batch_op:
        batch_op.drop_constraint('fk_ml_model_customer_id_customer', type_='foreignkey')
        batch_op.drop_index('ix_ml_model_customer_id')
        batch_op.drop_column('customer_id')
        batch_op.alter_column('tenant_id', existing_type=sa.Uuid(), nullable=False)
    if postgres:
        op.execute('ALTER TABLE "ml_model" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "ml_model" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_ml_model ON "ml_model" USING ({_TENANT})')

    op.drop_index(op.f('ix_customer_ml_setting_customer_id'), table_name='customer_ml_setting')
    op.drop_index(op.f('ix_customer_ml_setting_tenant_id'), table_name='customer_ml_setting')
    op.drop_table('customer_ml_setting')

    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('ml_scoring_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.drop_constraint('fk_tenant_ml_shared_consent_by_user', type_='foreignkey')
        batch_op.drop_constraint('fk_tenant_ml_source_changed_by_user', type_='foreignkey')
        for column in ('ml_shared_consent_by_user_id', 'ml_shared_consent_at', 'ml_private_switch_allowed_at',
                       'ml_source_changed_by_user_id', 'ml_source_changed_at', 'ml_model_source'):
            batch_op.drop_column(column)

    with op.batch_alter_table('exception_item') as batch_op:
        for column in ('shared_training_approved_at', 'retracted_by_user_id', 'retracted_at', 'is_correction'):
            batch_op.drop_column(column)


def downgrade() -> None:
    with op.batch_alter_table('exception_item') as batch_op:
        batch_op.add_column(sa.Column('is_correction', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('retracted_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('retracted_by_user_id', sa.Uuid(), sa.ForeignKey('user.id'), nullable=True))
        batch_op.add_column(sa.Column('shared_training_approved_at', sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table('tenant') as batch_op:
        batch_op.add_column(sa.Column('ml_model_source', sa.Enum('SHARED', 'PRIVATE', name='ml_model_source', native_enum=False, length=10),
                                      nullable=False, server_default='SHARED'))
        batch_op.add_column(sa.Column('ml_source_changed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('ml_source_changed_by_user_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('ml_private_switch_allowed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('ml_shared_consent_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('ml_shared_consent_by_user_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key('fk_tenant_ml_source_changed_by_user', 'user', ['ml_source_changed_by_user_id'], ['id'])
        batch_op.create_foreign_key('fk_tenant_ml_shared_consent_by_user', 'user', ['ml_shared_consent_by_user_id'], ['id'])
        batch_op.drop_column('ml_scoring_enabled')

    op.create_table(
        'customer_ml_setting',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('customer_id', sa.Uuid(), nullable=False),
        sa.Column('network_code', sa.String(length=32), nullable=False),
        sa.Column('mode', sa.Enum('AUTO', 'GLOBAL', 'CUSTOMER', name='ml_scoring_mode', native_enum=False, length=10), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_customer_ml_setting_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'], name=op.f('fk_customer_ml_setting_customer_id_customer')),
        sa.ForeignKeyConstraint(['network_code'], ['payment_network.code'],
                                name=op.f('fk_customer_ml_setting_network_code_payment_network')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_customer_ml_setting')),
        sa.UniqueConstraint('customer_id', 'network_code', name='uq_customer_ml_setting_customer_network'),
    )
    op.create_index(op.f('ix_customer_ml_setting_tenant_id'), 'customer_ml_setting', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_customer_ml_setting_customer_id'), 'customer_ml_setting', ['customer_id'], unique=False)
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE "customer_ml_setting" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "customer_ml_setting" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_customer_ml_setting ON "customer_ml_setting" USING ({_TENANT})')

    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_ml_model ON "ml_model"')
        op.execute('ALTER TABLE "ml_model" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "ml_model" DISABLE ROW LEVEL SECURITY')
    with op.batch_alter_table('ml_model') as batch_op:
        batch_op.alter_column('tenant_id', existing_type=sa.Uuid(), nullable=True)
        batch_op.add_column(sa.Column('customer_id', sa.Uuid(), nullable=True))
        batch_op.create_index('ix_ml_model_customer_id', ['customer_id'], unique=False)
        batch_op.create_foreign_key('fk_ml_model_customer_id_customer', 'customer', ['customer_id'], ['id'])
