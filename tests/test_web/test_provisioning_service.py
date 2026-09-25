# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.auth.security import verify_password
from openclear.services.provisioning_service import create_tenant_with_admin


def test_create_tenant_with_admin(db_session):
    identity = create_tenant_with_admin(
        db_session,
        tenant_name="Acme Bank",
        tenant_slug="acme-bank",
        admin_email="admin@acme.example.com",
        admin_password="hunter2-hunter2",
    )
    db_session.commit()

    assert identity.tenant.slug == "acme-bank"
    assert identity.membership.tenant_id == identity.tenant.id
    assert identity.membership.user_id == identity.admin_user.id
    assert verify_password("hunter2-hunter2", identity.admin_user.hashed_password)
