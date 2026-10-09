# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""API clients (SDK_PLAN.md P0): OAuth 2.0 client credentials for a bank's systems, acting
through a service identity that gets the same permission checks, customer scope and audit
trail as a member, and that nobody can sign in as."""

import base64
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.user import User
from openclear.services import (
    account_service,
    api_client_service,
    customer_service,
    issued_item_service,
    security_group_service,
    user_service,
)
from tests.conftest import TenantFactory


def _client(db_session, tenant, group_name="Admin", customer_id=None, name="Core sync"):
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, group_name)
    issued = api_client_service.create_client(db_session, tenant.id, name=name, security_group_id=group.id, customer_id=customer_id)
    db_session.commit()
    return issued


def _token(client, client_id, secret, *, basic=False):
    if basic:
        auth = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        return client.post("/api/v1/oauth/token", data={"grant_type": "client_credentials"}, headers={"Authorization": f"Basic {auth}"})
    return client.post("/api/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret})


def _bearer(client, issued, **kwargs) -> dict:
    resp = _token(client, issued.client.client_id, issued.secret, **kwargs)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 30 * 60
    assert resp.headers["cache-control"] == "no-store"
    return {"Authorization": f"Bearer {body['access_token']}"}


def test_a_client_gets_a_token_and_acts_with_its_groups_permissions(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="apic-basic")
    issued = _client(db_session, tenant)
    assert issued.client.client_id.startswith(f"occ_{tenant.id.hex}_") and issued.secret.startswith("ocs_")

    for basic in (False, True):  # form fields or HTTP Basic, both per RFC 6749
        headers = _bearer(client, issued, basic=basic)
        created = client.post("/api/v1/issued-items", headers=headers, json={
            "account_id": str(account.id), "check_number": f"77{int(basic)}", "amount": "12.00", "payee_name": "Vendor", "issue_date": "2026-03-01",
        })
        assert created.status_code == 201, created.text

    # Attributed in the audit log to the client's service identity.
    entry = db_session.execute(
        select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id, AuditLogEntry.action == "issued_item.create")
    ).scalars().first()
    assert entry.actor_user_id == issued.client.user_id
    db_session.expire_all()
    assert db_session.get(type(issued.client), issued.client.id).last_used_at is not None


def test_a_client_is_limited_by_its_group_and_customer(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="apic-scope")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    theirs = account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number="C1-1", name="Acme ops", customer_id=customer.id))
    for acct, number in ((account, "1"), (theirs, "2")):
        issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
            account_id=acct.id, check_number=number, amount=Decimal("5.00"), payee_name="X", issue_date=date(2026, 3, 1)), submitted_by_user_id=users["admin"].id)
    db_session.commit()

    viewer = _bearer(client, _client(db_session, tenant, group_name="Viewer", name="Reporting"))
    assert client.post("/api/v1/issued-items", headers=viewer, json={}).status_code == 403  # Viewer can't write
    assert len(client.get("/api/v1/issued-items", headers=viewer).json()) == 2

    scoped = _bearer(client, _client(db_session, tenant, group_name="Viewer", customer_id=customer.id, name="Acme ERP"))
    assert [i["check_number"] for i in client.get("/api/v1/issued-items", headers=scoped).json()] == ["2"]


@pytest.mark.parametrize("form", [
    {"grant_type": "client_credentials", "client_id": "occ_nope_123456789", "client_secret": "x"},
    {"grant_type": "client_credentials"},
])
def test_unknown_clients_are_refused(client, form):
    resp = client.post("/api/v1/oauth/token", data=form)
    assert resp.status_code == 401 and resp.json()["error"] == "invalid_client"


def test_wrong_secret_and_unsupported_grant(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="apic-wrong")
    issued = _client(db_session, tenant)
    assert _token(client, issued.client.client_id, "ocs_wrong").json()["error"] == "invalid_client"
    resp = client.post("/api/v1/oauth/token", data={"grant_type": "password", "client_id": issued.client.client_id, "client_secret": issued.secret})
    assert resp.status_code == 400 and resp.json()["error"] == "unsupported_grant_type"


def test_rotating_and_revoking(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="apic-rotate")
    issued = _client(db_session, tenant)
    headers = _bearer(client, issued)

    rotated = api_client_service.rotate_secret(db_session, tenant.id, issued.client.id)
    db_session.commit()
    assert _token(client, issued.client.client_id, issued.secret).status_code == 401  # the old secret is dead
    assert _token(client, issued.client.client_id, rotated.secret).status_code == 200
    assert client.get("/api/v1/issued-items", headers=headers).status_code == 200  # tokens already issued last until they expire

    api_client_service.revoke(db_session, tenant.id, issued.client.id)
    db_session.commit()
    assert client.get("/api/v1/issued-items", headers=headers).status_code == 401  # at once, not at expiry
    assert _token(client, issued.client.client_id, rotated.secret).status_code == 401


def test_nobody_can_sign_in_as_a_service_identity(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="apic-nologin")
    issued = _client(db_session, tenant)
    service = db_session.get(User, issued.client.user_id)
    assert service.is_service_account and service.email.endswith("@api-client.invalid")

    # Password login, even if someone learned (or set) a password.
    from openclear.auth.security import hash_password

    service.hashed_password = hash_password(TenantFactory.PASSWORD)
    db_session.commit()
    from openclear.auth.login_service import PasswordLoginOutcome, authenticate_password

    result = authenticate_password(db_session, tenant.slug, service.email, TenantFactory.PASSWORD)
    assert result.outcome == PasswordLoginOutcome.INVALID_CREDENTIALS
    db_session.rollback()

    # Not listed or managed with the bank's people, and can't be attached to by email.
    assert service.email not in [row.user.email for row in user_service.list_tenant_users(db_session, tenant.id)]
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    result = user_service.add_user(db_session, tenant.id, email=service.email, password="", security_group_id=group.id)
    assert result.outcome == "failed"


def test_sso_never_signs_in_as_a_service_identity(db_session, tenant_factory):
    from openclear.auth.oidc_service import OidcClaims
    from openclear.domain.sso_connection import SsoProvider
    from openclear.services import sso_service

    tenant, _account, _users = tenant_factory.make(slug="apic-sso")
    issued = _client(db_session, tenant)
    service = db_session.get(User, issued.client.user_id)
    connection = sso_service.create_connection(db_session, tenant.id, sso_service.SsoConnectionInput(
        provider=SsoProvider.OKTA, display_name="IdP", issuer="https://idp.example.com", client_id="c", client_secret="s",
        groups_claim_name="groups", auto_provision=True,
    ))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    sso_service.add_group_mapping(db_session, tenant.id, connection.id, external_group="admins", security_group_id=group.id)
    db_session.commit()

    claims = OidcClaims(subject="x", email=service.email, groups=["admins"])
    assert sso_service.complete_sso_login(db_session, tenant.id, connection, claims).outcome == "not_authorized"


def _web_login(client, tenant, email):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"})
    return csrf


def test_the_admin_page_creates_shows_the_secret_once_and_revokes(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="apic-web")
    csrf = _web_login(client, tenant, users["admin"].email)
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")

    page = client.post("/ui/admin/api-clients", data={"csrf_token": csrf, "name": "Core sync", "security_group_id": str(group.id), "customer_id": ""})
    assert page.status_code == 200 and "Copy the secret now" in page.text and page.headers["cache-control"] == "no-store"
    [made] = api_client_service.list_clients(db_session, tenant.id)
    assert made.client_id in page.text and "ocs_" in page.text

    listing = client.get("/ui/admin/api-clients")
    assert "Core sync" in listing.text and "Preparer" in listing.text and "ocs_" not in listing.text  # never shown again
    assert client.post("/ui/admin/api-clients", data={"csrf_token": csrf, "name": "", "security_group_id": str(group.id)}).status_code == 422

    revoked = client.post(f"/ui/admin/api-clients/{made.id}/revoke", data={"csrf_token": csrf}, follow_redirects=False)
    assert revoked.status_code == 303
    db_session.expire_all()
    assert api_client_service.list_clients(db_session, tenant.id)[0].revoked_at is not None
    actions = {e.action for e in db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id)).scalars()}
    assert {"api_client.create", "api_client.revoke"} <= actions


def test_only_api_client_managers_see_the_page(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="apic-web-forbidden")
    _web_login(client, tenant, users["viewer"].email)
    assert client.get("/ui/admin/api-clients").status_code == 403


def test_access_review_lists_clients_without_secrets(client, db_session, tenant_factory):
    from tests.conftest import login_headers

    tenant, _account, users = tenant_factory.make(slug="apic-review")
    issued = _client(db_session, tenant)
    rows = client.get("/api/v1/api-clients", headers=login_headers(client, tenant.slug, users["admin"].email)).json()
    assert [r["client_id"] for r in rows] == [issued.client.client_id] and "secret" not in str(rows).lower().replace("secret_rotated_at", "")
