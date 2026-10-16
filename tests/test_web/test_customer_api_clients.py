# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A customer's own API clients (MOBILE_PLAN.md R4; services/api_client_service.py,
web/routers/customer_api_clients.py): only while the bank allows it, only the roles it offers
and the customer's own accounts, never another customer's or the bank's clients."""

import re

from sqlalchemy import select

from openclear.domain.api_client import ApiClient
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.security_event import SecurityEvent
from openclear.services import api_client_service, security_group_service
from tests.conftest import login_headers
from tests.test_api.test_api_clients import _token
from tests.test_web.test_customer_people import _login, _setup


def _allow(db_session, s, *, on=True):
    s["admin_role"].permissions = sorted({*s["admin_role"].permissions, "customer_api_client:manage", "account:read"})
    s["clerk"].permissions = sorted({*s["clerk"].permissions, "account:read"})
    s["customer"].self_manages_api_clients = on
    db_session.commit()


def _create(client, csrf, s, name="Accounting system", *, group=None, accounts=None, read_only=False):
    data = {"csrf_token": csrf, "name": name, "security_group_id": str((group or s["clerk"]).id)}
    if accounts:
        data.update(scope="some", account_id=[str(a.id) for a in accounts])
    if read_only:
        data["read_only"] = "true"
    return client.post("/ui/api-clients", data=data, follow_redirects=False)


def _credentials(page: str) -> tuple[str, str]:
    client_id, secret = re.findall(r"<code>([^<]+)</code>", page)[:2]
    return client_id, secret


def test_api_clients_need_the_banks_permission(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cac-off")
    _allow(db_session, s, on=False)
    _login(client, s["tenant"], s["admin"].email)
    assert client.get("/ui/api-clients").status_code == 403
    assert "API clients" not in client.get("/ui/settings").text
    s["customer"].self_manages_api_clients = True
    db_session.commit()
    assert client.get("/ui/api-clients").status_code == 200
    assert 'href="/ui/api-clients"' in client.get("/ui/settings").text  # its tile
    s["customer"].self_manages_api_clients = False  # the bank stops it: at once
    db_session.commit()
    assert client.get("/ui/api-clients").status_code == 403


def test_a_bank_wide_session_never_holds_the_customer_permission(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cac-bank")
    _allow(db_session, s)
    _login(client, s["tenant"], s["users"]["admin"].email)  # the bank's Admin group has every permission
    assert client.get("/ui/api-clients").status_code == 403


def test_a_client_for_some_accounts_sees_only_those(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cac-make")
    _allow(db_session, s)
    csrf = _login(client, s["tenant"], s["admin"].email)
    page = _create(client, csrf, s, accounts=[s["payroll"]])
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    client_id, secret = _credentials(page.text)

    made = db_session.execute(select(ApiClient).where(ApiClient.client_id == client_id)).scalar_one()
    assert made.managed_by == "customer" and not made.can_act_for_users and made.sso_connection_id is None
    token = _token(client, client_id, secret)
    assert token.status_code == 200, token.text
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    assert [a["account_number"] for a in client.get("/api/v1/accounts", headers=headers).json()] == ["AC-PAY"]

    listing = client.get("/ui/api-clients").text
    assert "Accounting system" in listing and "AC-PAY" in listing


def test_only_offered_roles_and_own_accounts(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cac-guard")
    _allow(db_session, s)
    bank_only = security_group_service.create_security_group(db_session, s["tenant"].id, security_group_service.SecurityGroupInput(
        name="Bank ops", permissions=["issued_item:read"]))
    db_session.commit()
    csrf = _login(client, s["tenant"], s["admin"].email)
    refused = _create(client, csrf, s, group=bank_only)
    assert refused.status_code == 422 and "roles your bank offers" in refused.text
    refused = _create(client, csrf, s, accounts=[s["theirs"]])  # another customer's account
    assert refused.status_code == 422 and "this customer&#39;s accounts" in refused.text
    db_session.expire_all()
    assert db_session.execute(select(ApiClient).where(ApiClient.tenant_id == s["tenant"].id)).scalars().all() == []


def test_never_another_customers_or_the_banks_clients(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cac-mine")
    _allow(db_session, s)
    s["other_customer"].self_manages_api_clients = True
    db_session.commit()
    theirs = api_client_service.create_customer_client(db_session, s["tenant"].id, s["other_customer"].id, name="Beta ERP",
                                                       security_group_id=s["clerk"].id).client
    banks = api_client_service.create_client(db_session, s["tenant"].id, name="Online banking", security_group_id=s["clerk"].id,
                                             customer_id=s["customer"].id).client  # the bank's, limited to this customer
    db_session.commit()
    csrf = _login(client, s["tenant"], s["admin"].email)
    listing = client.get("/ui/api-clients").text
    assert "Beta ERP" not in listing and "Online banking" not in listing
    for target in (theirs, banks):
        for path in ("rotate", "revoke", "use-secret"):
            assert client.post(f"/ui/api-clients/{target.id}/{path}", data={"csrf_token": csrf}).status_code == 404
        assert client.get(f"/ui/api-clients/{target.id}/key").status_code == 404
    db_session.expire_all()
    assert db_session.get(ApiClient, theirs.id).revoked_at is None and db_session.get(ApiClient, banks.id).revoked_at is None


def test_rotate_key_pair_and_revoke(client, db_session, tenant_factory):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    s = _setup(db_session, tenant_factory, slug="cac-life")
    _allow(db_session, s)
    csrf = _login(client, s["tenant"], s["admin"].email)
    client_id, old_secret = _credentials(_create(client, csrf, s).text)
    made = db_session.execute(select(ApiClient).where(ApiClient.client_id == client_id)).scalar_one()

    _same_id, new_secret = _credentials(client.post(f"/ui/api-clients/{made.id}/rotate", data={"csrf_token": csrf}).text)
    assert _token(client, client_id, old_secret).status_code == 401 and _token(client, client_id, new_secret).status_code == 200

    pem = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    saved = client.post(f"/ui/api-clients/{made.id}/key", data={"csrf_token": csrf, "public_key_pem": pem}, follow_redirects=False)
    assert saved.status_code == 303 and _token(client, client_id, new_secret).status_code == 401  # the key pair only now

    assert client.post(f"/ui/api-clients/{made.id}/revoke", data={"csrf_token": csrf}, follow_redirects=False).status_code == 303
    db_session.expire_all()
    assert db_session.get(ApiClient, made.id).revoked_at is not None


def test_recorded_against_the_customer_and_as_security_events(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cac-audit")
    _allow(db_session, s)
    csrf = _login(client, s["tenant"], s["admin"].email)
    client_id, _secret = _credentials(_create(client, csrf, s, read_only=True).text)
    db_session.expire_all()
    entry = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == s["tenant"].id,
                                                           AuditLogEntry.action == "api_client.create")).scalar_one()
    assert entry.resource_type == "customer" and entry.resource_id == s["customer"].id and client_id in entry.summary
    assert "read-only API client Accounting system" in client.get("/ui/people/activity").text
    assert db_session.execute(select(SecurityEvent).where(SecurityEvent.tenant_id == s["tenant"].id,
                                                          SecurityEvent.name == "api_client.created")).scalars().first() is not None


def test_the_bank_sees_and_revokes_customers_clients(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cac-seen")
    _allow(db_session, s)
    made = api_client_service.create_customer_client(db_session, s["tenant"].id, s["customer"].id, name="Acme ERP",
                                                     security_group_id=s["clerk"].id).client
    db_session.commit()

    customer_api = client.get("/api/v1/api-clients", headers=login_headers(client, s["tenant"].slug, s["admin"].email))
    assert [c["name"] for c in customer_api.json()] == ["Acme ERP"] and customer_api.json()[0]["managed_by"] == "customer"
    bank_api = client.get("/api/v1/api-clients", headers=login_headers(client, s["tenant"].slug, s["users"]["admin"].email)).json()
    assert {"name": "Acme ERP", "managed_by": "customer", "customer": "Acme"}.items() <= next(c for c in bank_api if c["name"] == "Acme ERP").items()
    clerk = client.get("/api/v1/api-clients", headers=login_headers(client, s["tenant"].slug, s["users"]["viewer"].email))
    assert clerk.status_code == 403

    csrf = _login(client, s["tenant"], s["users"]["admin"].email)
    page = client.get("/ui/admin/api-clients").text
    assert "Acme ERP" in page and '<span class="badge">theirs</span>' in page
    assert client.post(f"/ui/admin/api-clients/{made.id}/revoke", data={"csrf_token": csrf}, follow_redirects=False).status_code == 303
    db_session.expire_all()
    assert db_session.get(ApiClient, made.id).revoked_at is not None


def test_the_banks_switch(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="cac-switch")
    headers = login_headers(client, s["tenant"].slug, s["users"]["admin"].email)
    body = client.put("/api/v1/customers/C-1/self-service", json={"manages_people": True, "manages_sign_on": False,
                                                                   "manages_api_clients": True}, headers=headers).json()
    assert body["manages_api_clients"] is True
    # Left out, it stays as it is.
    body = client.put("/api/v1/customers/C-1/self-service", json={"manages_people": True, "manages_sign_on": False}, headers=headers).json()
    assert body["manages_api_clients"] is True
    csrf = _login(client, s["tenant"], s["users"]["admin"].email)
    client.post(f"/ui/customers/{s['customer'].id}/self-service", data={"csrf_token": csrf, "manages_users": "true"})
    db_session.expire_all()
    assert s["customer"].self_manages_api_clients is False and s["customer"].self_manages_users is True
