# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""decision feed: decision.feed_sequence and decision_feed_cursor (SDK_PLAN.md P0);
existing live decisions are numbered per bank in the order they were made

Revision ID: e3a5c7d9f1b2
Revises: d2f4b6c8e0a1
Create Date: 2026-10-09 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3a5c7d9f1b2'
down_revision: Union[str, None] = 'd2f4b6c8e0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def upgrade() -> None:
    with op.batch_alter_table('decision') as batch_op:
        batch_op.add_column(sa.Column('feed_sequence', sa.BigInteger(), nullable=True))

    # Number the live decisions already made, per bank, oldest first (fraud-training
    # examples aren't items to pay or return, so they stay out of the feed).
    bind = op.get_bind()
    decision = sa.table('decision', sa.column('id', sa.Uuid()), sa.column('tenant_id', sa.Uuid()),
                        sa.column('exception_item_id', sa.Uuid()), sa.column('decided_at', sa.DateTime()),
                        sa.column('feed_sequence', sa.BigInteger()))
    exception = sa.table('exception_item', sa.column('id', sa.Uuid()), sa.column('source', sa.String()))
    rows = bind.execute(
        sa.select(decision.c.id, decision.c.tenant_id)
        .select_from(decision.join(exception, exception.c.id == decision.c.exception_item_id))
        .where(sa.or_(exception.c.source.is_(None), exception.c.source != 'TRAINING_BACKFILL'))
        .order_by(decision.c.tenant_id, decision.c.decided_at, decision.c.id)
    ).all()
    counters: dict = {}
    for decision_id, tenant_id in rows:
        counters[tenant_id] = counters.get(tenant_id, 0) + 1
        bind.execute(sa.update(decision).where(decision.c.id == decision_id).values(feed_sequence=counters[tenant_id]))

    with op.batch_alter_table('decision') as batch_op:
        batch_op.create_unique_constraint('uq_decision_tenant_feed_sequence', ['tenant_id', 'feed_sequence'])

    op.create_table(
        'decision_feed_cursor',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('consumer', sa.String(length=100), nullable=False),
        sa.Column('through_sequence', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_by_user_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_decision_feed_cursor_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['user.id'], name=op.f('fk_decision_feed_cursor_updated_by_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_decision_feed_cursor')),
        sa.UniqueConstraint('tenant_id', 'consumer', name='uq_decision_feed_cursor_consumer'),
    )
    op.create_index(op.f('ix_decision_feed_cursor_tenant_id'), 'decision_feed_cursor', ['tenant_id'])
    if bind.dialect.name == 'postgresql':
        op.execute('ALTER TABLE "decision_feed_cursor" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "decision_feed_cursor" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation_decision_feed_cursor ON "decision_feed_cursor" USING ({_TENANT})')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_decision_feed_cursor ON "decision_feed_cursor"')
    op.drop_index(op.f('ix_decision_feed_cursor_tenant_id'), table_name='decision_feed_cursor')
    op.drop_table('decision_feed_cursor')
    with op.batch_alter_table('decision') as batch_op:
        batch_op.drop_constraint('uq_decision_tenant_feed_sequence', type_='unique')
        batch_op.drop_column('feed_sequence')
