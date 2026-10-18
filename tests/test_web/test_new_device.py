# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""New-device sign-in emails (OWASP_PLAN.md OW-D30; ASVS 5.0 6.3.5): a person's first browser is
recorded quietly; another browser emails them; a browser they've used before doesn't."""

import pytest
from fastapi.testclient import TestClient

from openclear.domain.notification import Notification, NotificationType
from tests.conftest import TenantFactory


def _sign_in(client, tenant, user):
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": user.email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})


def _emails(db_session, user):
    db_session.expire_all()
    return db_session.query(Notification).filter(Notification.recipient_user_id == user.id,
                                                 Notification.notification_type == NotificationType.NEW_DEVICE_SIGN_IN).all()


@pytest.mark.asvs("6.3.5")
def test_a_new_browser_emails_the_person_and_a_known_one_doesnt(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="new-device")
    viewer = users["viewer"]

    _sign_in(client, tenant, viewer)
    assert _emails(db_session, viewer) == []  # the first browser ever: no email

    client.post("/ui/logout", data={"csrf_token": client.cookies.get("csrf_token")})
    _sign_in(client, tenant, viewer)
    assert _emails(db_session, viewer) == []  # the same browser again

    other = TestClient(client.app)
    other.headers["user-agent"] = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Safari/604.1"
    _sign_in(other, tenant, viewer)
    [email] = _emails(db_session, viewer)
    assert "Safari on iPhone" in email.body
