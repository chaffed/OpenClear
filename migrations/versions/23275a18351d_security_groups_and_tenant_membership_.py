# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""security groups and tenant membership, replace user role with security group

Revision ID: 23275a18351d
Revises: f48f7301f98a
Create Date: 2026-07-24 15:33:03.048155

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '23275a18351d'
down_revision: Union[str, None] = 'f48f7301f98a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Snapshot of auth/permissions.py's catalog as of this migration — deliberately NOT
# imported from the live module (which will keep evolving) so this migration always
# backfills the same result no matter how the catalog changes later.
_ALL_PERMISSIONS = [
    "account:read", "account:write",
    "issued_item:read", "issued_item:write",
    "stop_payment:read", "stop_payment:write",
    "paid_item:read", "paid_item:write",
    "check_image:read", "check_image:write",
    "ach_authorization:read", "ach_authorization:write",
    "ach_transaction:read", "ach_transaction:write",
    "exception:read", "exception:recommend", "exception:decide",
    "admin:manage", "user:manage", "security_group:manage",
]
_READS = [p for p in _ALL_PERMISSIONS if p.endswith(":read")]
_DEFAULT_GROUPS = {
    "Admin": _ALL_PERMISSIONS,
    "Preparer": [
        "account:read",
        "issued_item:read", "issued_item:write",
        "stop_payment:read", "stop_payment:write",
        "paid_item:read", "paid_item:write",
        "check_image:read", "check_image:write",
        "ach_authorization:read", "ach_authorization:write",
        "ach_transaction:read", "ach_transaction:write",
        "exception:read", "exception:recommend",
    ],
    "Approver": [*_READS, "exception:decide"],
    "Viewer": [*_READS],
}
# Old UserRole enum values (auth/rbac.py, now deleted) -> the seeded group with the same name.
_ROLE_TO_GROUP_NAME = {"admin": "Admin", "preparer": "Preparer", "approver": "Approver", "viewer": "Viewer"}


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        'security_group',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_security_group_tenant_id_tenant')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_security_group')),
        sa.UniqueConstraint('tenant_id', 'name', name='uq_security_group_tenant_name'),
    )
    op.create_index(op.f('ix_security_group_tenant_id'), 'security_group', ['tenant_id'], unique=False)

    op.create_table(
        'tenant_membership',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.Uuid(), nullable=False),
        sa.Column('security_group_id', sa.Uuid(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(
            ['security_group_id'], ['security_group.id'], name=op.f('fk_tenant_membership_security_group_id_security_group')
        ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_tenant_membership_tenant_id_tenant')),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_tenant_membership_user_id_user')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tenant_membership')),
        sa.UniqueConstraint('user_id', 'tenant_id', name='uq_tenant_membership_user_tenant'),
    )
    op.create_index(op.f('ix_tenant_membership_user_id'), 'tenant_membership', ['user_id'], unique=False)
    op.create_index(op.f('ix_tenant_membership_tenant_id'), 'tenant_membership', ['tenant_id'], unique=False)

    # --- data backfill: seed the 4 default groups per existing tenant, then a
    # tenant_membership per existing user mapping their old role to the matching group —
    # all while user.tenant_id/user.role still exist to read from below. ---
    tenant_t = sa.table('tenant', sa.column('id', sa.Uuid()))
    user_t = sa.table('user', sa.column('id', sa.Uuid()), sa.column('tenant_id', sa.Uuid()), sa.column('role', sa.String()))
    security_group_t = sa.table(
        'security_group',
        sa.column('id', sa.Uuid()),
        sa.column('tenant_id', sa.Uuid()),
        sa.column('name', sa.String()),
        sa.column('permissions', sa.JSON()),
    )
    membership_t = sa.table(
        'tenant_membership',
        sa.column('id', sa.Uuid()),
        sa.column('user_id', sa.Uuid()),
        sa.column('tenant_id', sa.Uuid()),
        sa.column('security_group_id', sa.Uuid()),
        sa.column('is_active', sa.Boolean()),
    )

    tenant_ids = [row.id for row in bind.execute(sa.select(tenant_t.c.id))]
    group_id_by_tenant_and_name: dict[tuple, uuid.UUID] = {}
    for tenant_id in tenant_ids:
        for name, permissions in _DEFAULT_GROUPS.items():
            group_id = uuid.uuid4()
            bind.execute(security_group_t.insert().values(id=group_id, tenant_id=tenant_id, name=name, permissions=permissions))
            group_id_by_tenant_and_name[(tenant_id, name)] = group_id

    users = bind.execute(sa.select(user_t.c.id, user_t.c.tenant_id, user_t.c.role)).all()
    for user_id, user_tenant_id, role in users:
        group_name = _ROLE_TO_GROUP_NAME.get(str(role).lower(), "Viewer")
        group_id = group_id_by_tenant_and_name.get((user_tenant_id, group_name))
        if group_id is None:
            continue
        bind.execute(
            membership_t.insert().values(
                id=uuid.uuid4(), user_id=user_id, tenant_id=user_tenant_id, security_group_id=group_id, is_active=True
            )
        )

    # --- now safe to drop the old per-tenant identity columns from user ---
    # On Postgres, the RLS migration (6b9b23b81e31) put a tenant_id policy on "user". A user
    # is now a global identity with no single owning tenant, so the policy goes (and RLS
    # with it) before its column can be dropped. Without this, upgrading a fresh Postgres
    # database failed here.
    if bind.dialect.name == 'postgresql':
        op.execute('DROP POLICY IF EXISTS tenant_isolation_user ON "user"')
        op.execute('ALTER TABLE "user" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "user" DISABLE ROW LEVEL SECURITY')
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_constraint('uq_user_tenant_email', type_='unique')
        batch_op.drop_index('ix_user_tenant_id')
        batch_op.drop_column('role')
        batch_op.drop_column('tenant_id')
        batch_op.create_index(op.f('ix_user_email'), ['email'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index(op.f('ix_user_email'))
        batch_op.add_column(sa.Column('tenant_id', sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column('role', sa.String(length=20), nullable=True))

    op.drop_index(op.f('ix_tenant_membership_tenant_id'), table_name='tenant_membership')
    op.drop_index(op.f('ix_tenant_membership_user_id'), table_name='tenant_membership')
    op.drop_table('tenant_membership')
    op.drop_index(op.f('ix_security_group_tenant_id'), table_name='security_group')
    op.drop_table('security_group')

    # user.tenant_id/role are left nullable with no data restored — downgrading past this
    # migration is a last resort (e.g. a bad deploy), not a supported round-trip; restoring
    # per-tenant identities from tenant_membership rows isn't well-defined once a user could
    # belong to more than one tenant.
