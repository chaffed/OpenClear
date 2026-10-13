# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A customer's own administrators setting up its sign-on (CUSTOMER_ADMIN_PLAN.md CA3;
services/customer_sign_on_service.py, web/routers/sign_on.py), signing in through it for
real (a fake OIDC provider), and the guardrails: offered roles only, verified domains before
anyone new is created, and no turning off passwords without seeing sign-on work."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from openclear.domain.user import User
from openclear.services import customer_sign_on_service, security_group_service, sso_service
from openclear.services.sso_service import SsoConnectionInput
from openclear.domain.sso_connection import SsoProvider
from tests.test_auth.oidc_helpers import FakeOidcProvider, patch_provider
from tests.test_web.test_customer_people import _login, _setup
from tests.test_web.test_web_sso_login import _start_sso, _state

ISSUER = "https://idp.example.com"


def _sign_on_setup(db_session, tenant_factory, slug):
    s = _setup(db_session, tenant_factory, slug=slug)
    s["admin_role"].permissions = [*s["admin_role"].permissions, "customer_sso:manage"]
    s["customer"].self_manages_sso = True
    db_session.commit()
    return s


def _connection(client, csrf, s, db_session, *, auto_provision=True):
    data = {"csrf_token": csrf, "provider": SsoProvider.OKTA.value, "display_name": "Acme Okta", "issuer": ISSUER,
            "client_id": "acme-client", "client_secret": "acme-secret", "groups_claim_name": "groups"}
    if auto_provision:
        data["auto_provision"] = "true"
    created = client.post("/ui/sign-on", data=data, follow_redirects=False)
    assert created.status_code == 303, created.text
    connection = sso_service.list_connections(db_session, s["tenant"].id, customer_id=s["customer"].id)[-1]
    assert connection.managed_by == "customer"
    return connection


def _sign_in(client, connection, tenant, monkeypatch, email, groups=("acme-staff",), next_path="/ui/"):
    nonce = _start_sso(client, connection, tenant.id, monkeypatch, next_path=next_path)
    provider = FakeOidcProvider(issuer=connection.issuer, client_id=connection.client_id)
    with pytest.MonkeyPatch.context() as patched:  # just this sign-in's answer
        patch_provider(patched, provider, provider.sign_id_token(sub=f"sub-{email}", email=email, groups=list(groups), nonce=nonce))
        return client.get(f"/ui/login/sso/{connection.id}/callback", params={"code": "c", "state": _state(client)}, follow_redirects=False)


def test_sign_on_needs_the_banks_permission(client, db_session, tenant_factory):
    s = _setup(db_session, tenant_factory, slug="so-off")
    s["admin_role"].permissions = [*s["admin_role"].permissions, "customer_sso:manage"]
    db_session.commit()
    _login(client, s["tenant"], s["admin"].email)
    assert client.get("/ui/sign-on").status_code == 403
    s["customer"].self_manages_sso = True
    db_session.commit()
    assert client.get("/ui/sign-on").status_code == 200
    assert "Sign-on" in client.get("/ui/settings").text


def test_a_customer_connects_its_identity_provider_and_its_people_sign_in(client, db_session, tenant_factory, monkeypatch):
    s = _sign_on_setup(db_session, tenant_factory, "so-flow")
    csrf = _login(client, s["tenant"], s["admin"].email)
    connection = _connection(client, csrf, s, db_session)

    bank_admin = security_group_service.get_security_group_by_name(db_session, s["tenant"].id, "Admin")
    refused = client.post(f"/ui/sign-on/{connection.id}/mappings", data={"csrf_token": csrf, "external_group": "acme-admins",
                                                                         "security_group_id": str(bank_admin.id)}, follow_redirects=False)
    assert "roles your bank offers" in refused.headers["location"].replace("+", " ").replace("%20", " ")
    assert client.post(f"/ui/sign-on/{connection.id}/mappings", data={"csrf_token": csrf, "external_group": "acme-staff",
                                                                      "security_group_id": str(s["clerk"].id)}, follow_redirects=False).status_code == 303
    assert "working" in client.get("/ui/sign-on").text

    # Someone new, in a domain not yet verified: not created.
    client.cookies.clear()
    denied = _sign_in(client, connection, s["tenant"], monkeypatch, "new@acme.example.com")
    assert denied.status_code != 303 or denied.headers["location"] != "/ui/"
    assert db_session.execute(select(User).where(User.email == "new@acme.example.com")).scalar_one_or_none() is None

    # Verify acme.example.com (its TXT record), and they're created on sign-in, confined to the customer.
    csrf = _login(client, s["tenant"], s["admin"].email)
    client.post("/ui/sign-on/domains", data={"csrf_token": csrf, "domain": "Acme.Example.com"})
    [domain] = customer_sign_on_service.domains(db_session, s["tenant"].id, s["customer"].id)
    assert domain.domain == "acme.example.com"
    monkeypatch.setattr(customer_sign_on_service, "txt_records", lambda name: [])
    assert "error=" in client.post(f"/ui/sign-on/domains/{domain.id}/verify", data={"csrf_token": csrf}, follow_redirects=False).headers["location"]
    monkeypatch.setattr(customer_sign_on_service, "txt_records",
                        lambda name: [f"openclear-verification={domain.verification_token}"] if name == "_openclear.acme.example.com" else [])
    client.post(f"/ui/sign-on/domains/{domain.id}/verify", data={"csrf_token": csrf})
    client.cookies.clear()
    signed_in = _sign_in(client, connection, s["tenant"], monkeypatch, "new@acme.example.com")
    assert signed_in.status_code == 303 and signed_in.headers["location"] == "/ui/", signed_in.text[:300]
    from openclear.auth.security import decode_token

    assert decode_token(client.cookies.get("access_token"))["cfd"] == 1
    # Another domain is still refused.
    client.cookies.clear()
    _sign_in(client, connection, s["tenant"], monkeypatch, "someone@elsewhere.example.org")
    assert db_session.execute(select(User).where(User.email == "someone@elsewhere.example.org")).scalar_one_or_none() is None


def test_a_customers_directory_creates_people_only_in_verified_domains(db_session, tenant_factory):
    from openclear.services import scim_service

    s = _sign_on_setup(db_session, tenant_factory, "so-scim")
    connection = customer_sign_on_service.create(db_session, s["tenant"].id, s["customer"].id, SsoConnectionInput(
        provider=SsoProvider.OKTA, display_name="Acme", issuer=ISSUER, client_id="c", client_secret="s", groups_claim_name="groups",
        auto_provision=True))
    db_session.commit()
    payload = {"userName": "pat@acme.example.com", "emails": [{"value": "pat@acme.example.com", "primary": True}], "active": True}
    try:
        scim_service.create_user(db_session, connection, payload)
        raise AssertionError("created a person in an unverified domain")
    except scim_service.ScimError as exc:
        assert exc.status == 409
    db_session.rollback()
    row = customer_sign_on_service.add_domain(db_session, s["tenant"].id, s["customer"].id, "acme.example.com")
    row.verified_at = datetime.now(timezone.utc)
    db_session.commit()
    assert scim_service.create_user(db_session, connection, payload).user_name == "pat@acme.example.com"


def test_passwords_go_off_only_after_seeing_sign_on_work(client, db_session, tenant_factory, monkeypatch):
    s = _sign_on_setup(db_session, tenant_factory, "so-passwords")
    csrf = _login(client, s["tenant"], s["admin"].email)
    connection = _connection(client, csrf, s, db_session, auto_provision=False)
    client.post(f"/ui/sign-on/{connection.id}/mappings", data={"csrf_token": csrf, "external_group": "acme-admins",
                                                               "security_group_id": str(s["admin_role"].id)})
    refused = client.post("/ui/sign-on/password-sign-in", data={"csrf_token": csrf}, follow_redirects=False)
    assert "error=" in refused.headers["location"]

    # "Test sign-in": the administrator signs in through the connection, then turns passwords off.
    client.cookies.clear()
    assert _sign_in(client, connection, s["tenant"], monkeypatch, s["admin"].email, groups=["acme-admins"], next_path="/ui/sign-on").status_code == 303
    client.get("/ui/sign-on")
    csrf = client.cookies.get("csrf_token")
    done = client.post("/ui/sign-on/password-sign-in", data={"csrf_token": csrf}, follow_redirects=False)
    assert "flash=" in done.headers["location"]
    db_session.expire_all()
    assert not db_session.get(type(s["customer"]), s["customer"].id).password_login_enabled

    # Its only connection can't then be turned off, nor lose its last mapping.
    off = client.post(f"/ui/sign-on/{connection.id}/deactivate", data={"csrf_token": csrf}, follow_redirects=False)
    assert "error=" in off.headers["location"]
    [mapping] = sso_service.list_group_mappings(db_session, s["tenant"].id, connection.id)
    unmapped = client.post(f"/ui/sign-on/{connection.id}/mappings/{mapping.id}/delete", data={"csrf_token": csrf}, follow_redirects=False)
    assert "error=" in unmapped.headers["location"]
    assert client.post("/ui/sign-on/password-sign-in", data={"csrf_token": csrf, "allowed": "true"}, follow_redirects=False).status_code == 303


def test_domains_and_connections_stay_the_customers_own(client, db_session, tenant_factory):
    s = _sign_on_setup(db_session, tenant_factory, "so-own")
    bank_made = sso_service.create_connection(db_session, s["tenant"].id, SsoConnectionInput(
        provider=SsoProvider.OKTA, display_name="Set up by the bank", issuer=ISSUER, client_id="c", client_secret="s",
        groups_claim_name="groups", auto_provision=False, customer_id=s["customer"].id))
    others = sso_service.create_connection(db_session, s["tenant"].id, SsoConnectionInput(
        provider=SsoProvider.OKTA, display_name="Beta's", issuer=ISSUER, client_id="c", client_secret="s",
        groups_claim_name="groups", auto_provision=False, customer_id=s["other_customer"].id))
    s["other_customer"].self_manages_sso = True
    customer_sign_on_service.add_domain(db_session, s["tenant"].id, s["other_customer"].id, "beta.example.com")
    db_session.commit()
    csrf = _login(client, s["tenant"], s["admin"].email)

    page = client.get("/ui/sign-on").text
    assert "Set up by the bank" in page and "Beta&#39;s" not in page and "Beta's" not in page
    assert client.get(f"/ui/sign-on/{bank_made.id}/edit").status_code == 404
    assert client.get(f"/ui/sign-on/{others.id}/edit").status_code == 404
    assert client.post(f"/ui/sign-on/{others.id}/scim-token", data={"csrf_token": csrf}).status_code == 404
    for domain, message in (("gmail.com", "Anyone can have"), ("beta.example.com", "another customer"), ("not a domain", "such as")):
        resp = client.post("/ui/sign-on/domains", data={"csrf_token": csrf, "domain": domain}, follow_redirects=False)
        assert message.replace(" ", "+") in resp.headers["location"] or message.replace(" ", "%20") in resp.headers["location"], domain

    assert client.get(f"/ui/customers/{s['customer'].id}/password-policy").status_code == 200
    assert client.get(f"/ui/customers/{s['other_customer'].id}/password-policy").status_code == 404
    assert client.get("/ui/admin/sso").status_code == 403
