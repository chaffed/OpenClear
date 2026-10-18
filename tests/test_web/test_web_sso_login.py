# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest
from openclear.auth.security import decode_sso_state_token
from openclear.domain.sso_connection import SsoProvider
from openclear.services import security_group_service, sso_service
from openclear.services.sso_service import SsoConnectionInput
from tests.conftest import TenantFactory
from tests.test_auth.oidc_helpers import FakeOidcProvider, patch_provider


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    return client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )


def _csrf(client):
    return client.cookies.get("csrf_token")


def _make_mapped_connection(db_session, tenant, *, customer_id=None, auto_provision=True, issuer="https://idp.example.com", external_group="openclear-users", group_name="Viewer"):
    data = SsoConnectionInput(
        provider=SsoProvider.OKTA, display_name="Test Okta", issuer=issuer, client_id="test-client-id",
        client_secret="hunter2-secret", groups_claim_name="groups", auto_provision=auto_provision, customer_id=customer_id,
    )
    connection = sso_service.create_connection(db_session, tenant.id, data)
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, group_name)
    sso_service.add_group_mapping(db_session, tenant.id, connection.id, external_group=external_group, security_group_id=group.id)
    db_session.commit()
    return connection, group


def _start_sso(client, connection, tenant_id, monkeypatch, next_path="/ui/"):
    # /start itself calls build_authorization_url, which fetches the discovery
    # document — patch that out here too, not just for the callback's token exchange.
    import openclear.auth.oidc_service as oidc_service

    provider = FakeOidcProvider(issuer=connection.issuer, client_id=connection.client_id)
    monkeypatch.setattr(oidc_service, "_get_discovery_document", lambda issuer: provider.discovery_document())

    resp = client.get(
        f"/ui/login/sso/{connection.id}/start", params={"tenant_id": str(tenant_id), "next": next_path}, follow_redirects=False
    )
    assert resp.status_code == 303, resp.text
    state_cookie = client.cookies.get("sso_state")
    assert state_cookie is not None
    state_claims = decode_sso_state_token(state_cookie)
    return state_claims["nonce"]


def _state(client) -> str:
    """The OAuth `state` /start sent to the identity provider, which a real provider echoes
    back on the callback (the server checks it matches its signed sso_state cookie)."""
    return decode_sso_state_token(client.cookies.get("sso_state"))["state"]


def test_branded_login_page_shows_sso_button_once_mapped(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-sso-button")
    connection, _group = _make_mapped_connection(db_session, tenant)

    resp = client.get(f"/ui/login/{tenant.slug}")

    assert resp.status_code == 200
    assert "Test Okta" in resp.text
    assert f"/ui/login/sso/{connection.id}/start" in resp.text


def test_full_sso_login_flow_auto_provisions_new_user(client, db_session, tenant_factory, monkeypatch):
    tenant, _account, _users = tenant_factory.make(slug="web-sso-flow")
    connection, group = _make_mapped_connection(db_session, tenant)

    nonce = _start_sso(client, connection, tenant.id, monkeypatch)

    provider = FakeOidcProvider(issuer=connection.issuer, client_id=connection.client_id)
    id_token = provider.sign_id_token(sub="sub-1", email="newperson@example.com", groups=["openclear-users"], nonce=nonce)
    patch_provider(monkeypatch, provider, id_token)

    resp = client.get(
        f"/ui/login/sso/{connection.id}/callback", params={"code": "fake-code", "state": _state(client)}, follow_redirects=False
    )

    assert resp.status_code == 303, resp.text
    assert resp.headers["location"] == "/ui/"
    assert client.cookies.get("access_token") is not None
    # PKCE: the token request carried the verifier /start kept in the signed state cookie.
    [token_request] = provider.token_requests
    assert len(token_request["code_verifier"]) >= 43

    from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
    from openclear.repositories.user_repo import UserRepository

    db_session.expire_all()
    user = UserRepository(db_session).get_by_email("newperson@example.com")
    assert user is not None
    membership = [m for m in TenantMembershipRepository(db_session, tenant.id).list(user_id=user.id) if m.customer_id is None][0]
    assert membership.security_group_id == group.id


def test_sso_login_not_authorized_shows_error_and_sets_no_cookies(client, db_session, tenant_factory, monkeypatch):
    tenant, _account, _users = tenant_factory.make(slug="web-sso-notauth")
    connection, _group = _make_mapped_connection(db_session, tenant)
    nonce = _start_sso(client, connection, tenant.id, monkeypatch)

    provider = FakeOidcProvider(issuer=connection.issuer, client_id=connection.client_id)
    id_token = provider.sign_id_token(sub="sub-1", email="stranger@example.com", groups=["not-mapped"], nonce=nonce)
    patch_provider(monkeypatch, provider, id_token)

    resp = client.get(f"/ui/login/sso/{connection.id}/callback", params={"code": "c", "state": _state(client)})

    assert resp.status_code == 401
    assert "not authorized" in resp.text.lower()
    assert client.cookies.get("access_token") is None


def test_sso_login_not_provisioned_shows_error(client, db_session, tenant_factory, monkeypatch):
    tenant, _account, _users = tenant_factory.make(slug="web-sso-notprov")
    connection, _group = _make_mapped_connection(db_session, tenant, auto_provision=False)
    nonce = _start_sso(client, connection, tenant.id, monkeypatch)

    provider = FakeOidcProvider(issuer=connection.issuer, client_id=connection.client_id)
    id_token = provider.sign_id_token(sub="sub-1", email="newperson@example.com", groups=["openclear-users"], nonce=nonce)
    patch_provider(monkeypatch, provider, id_token)

    resp = client.get(f"/ui/login/sso/{connection.id}/callback", params={"code": "c", "state": _state(client)})

    assert resp.status_code == 401
    assert "no openclear account" in resp.text.lower()


def test_sso_callback_missing_state_cookie_shows_error(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-sso-nocookie")
    connection, _group = _make_mapped_connection(db_session, tenant)

    resp = client.get(f"/ui/login/sso/{connection.id}/callback", params={"code": "c", "state": "s"})

    assert resp.status_code == 401
    assert "cancelled or failed" in resp.text.lower()


def test_customer_branded_login_shows_only_that_customers_connection(client, db_session, tenant_factory):
    from openclear.services import customer_service

    tenant, _account, _users = tenant_factory.make(slug="web-sso-custlogin")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme Co"))
    db_session.commit()
    bank_connection, _g1 = _make_mapped_connection(db_session, tenant, external_group="bank-group")
    customer_connection, _g2 = _make_mapped_connection(
        db_session, tenant, customer_id=customer.id, issuer="https://customer-idp.example.com", external_group="cust-group"
    )
    sso_service.update_connection(
        db_session, tenant.id, customer_connection.id,
        SsoConnectionInput(
            provider=SsoProvider.AZURE_AD, display_name="Customer Azure AD", issuer="https://customer-idp.example.com",
            client_id="test-client-id", client_secret=None, groups_claim_name="groups", auto_provision=True, customer_id=customer.id,
        ),
    )
    db_session.commit()

    bank_page = client.get(f"/ui/login/{tenant.slug}")
    assert bank_connection.display_name in bank_page.text
    assert "Customer Azure AD" not in bank_page.text

    customer_page = client.get(f"/ui/login/{tenant.slug}/{customer.customer_number}")
    assert "Customer Azure AD" in customer_page.text
    assert bank_connection.display_name not in customer_page.text


def test_password_login_hidden_when_tenant_requires_sso(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-sso-hidepw")
    _make_mapped_connection(db_session, tenant)
    sso_service.set_tenant_password_login_enabled(db_session, tenant.id, enabled=False)
    db_session.commit()

    resp = client.get(f"/ui/login/{tenant.slug}")

    assert resp.status_code == 200
    assert 'name="password"' not in resp.text


def test_password_login_rejected_with_sso_required_message_if_submitted_anyway(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-sso-pwrejected")
    _make_mapped_connection(db_session, tenant)
    sso_service.set_tenant_password_login_enabled(db_session, tenant.id, enabled=False)
    db_session.commit()

    resp = _login(client, tenant.slug, users["admin"].email)

    assert resp.status_code == 401
    assert "single sign-on" in resp.text.lower()


# --- Admin CRUD (bank-wide) ---


def test_bank_sso_settings_requires_tenant_manage_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-sso-admin-forbidden")
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get("/ui/admin/sso", follow_redirects=False)

    assert resp.status_code == 403


def test_create_bank_connection_and_add_mapping_via_web(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-sso-admin-create")
    _login(client, tenant.slug, users["admin"].email)
    client.get("/ui/admin/sso/new")

    create_resp = client.post(
        "/ui/admin/sso",
        data={
            "provider": "okta", "display_name": "New Connection", "issuer": "https://idp.example.com",
            "client_id": "cid", "client_secret": "shh", "groups_claim_name": "groups",
            "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )
    assert create_resp.status_code == 303
    connection_id = create_resp.headers["location"].split("/ui/admin/sso/")[1].split("/edit")[0]

    # Not usable as a login option yet — zero mappings.
    assert sso_service.get_login_connections(db_session, tenant.id) == []

    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    map_resp = client.post(
        f"/ui/admin/sso/{connection_id}/mappings",
        data={"external_group": "ext-group", "security_group_id": str(group.id), "priority": "100", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert map_resp.status_code == 303

    db_session.expire_all()
    assert len(sso_service.get_login_connections(db_session, tenant.id)) == 1


def test_deactivate_and_reactivate_bank_connection_via_web(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-sso-admin-deact")
    connection, _group = _make_mapped_connection(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)
    client.get("/ui/admin/sso")

    deact = client.post(f"/ui/admin/sso/{connection.id}/deactivate", data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert deact.status_code == 303
    db_session.expire_all()
    assert sso_service.get_connection(db_session, tenant.id, connection.id).is_active is False

    react = client.post(f"/ui/admin/sso/{connection.id}/reactivate", data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert react.status_code == 303
    db_session.expire_all()
    assert sso_service.get_connection(db_session, tenant.id, connection.id).is_active is True


def test_require_sso_toggle_via_web_guards_against_lockout(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-sso-admin-toggle")
    _login(client, tenant.slug, users["admin"].email)
    client.get("/ui/admin/sso")

    # No connections yet — attempting to require SSO must fail with an error, not lock everyone out.
    resp = client.post(
        "/ui/admin/sso/password-login", data={"require_sso": "true", "csrf_token": _csrf(client)}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]

    _make_mapped_connection(db_session, tenant)
    resp2 = client.post(
        "/ui/admin/sso/password-login", data={"require_sso": "true", "csrf_token": _csrf(client)}, follow_redirects=False
    )
    assert resp2.status_code == 303
    assert "error" not in resp2.headers["location"]


# --- Admin CRUD (per-customer) ---


def test_customer_sso_settings_requires_customer_manage_permission(client, db_session, tenant_factory):
    from openclear.services import customer_service

    tenant, _account, users = tenant_factory.make(slug="web-sso-cust-forbidden")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    db_session.commit()
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get(f"/ui/customers/{customer.id}/sso", follow_redirects=False)

    assert resp.status_code == 403


def test_create_customer_connection_via_web(client, db_session, tenant_factory):
    from openclear.services import customer_service

    tenant, _account, users = tenant_factory.make(slug="web-sso-cust-create")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)
    client.get(f"/ui/customers/{customer.id}/sso/new")

    resp = client.post(
        f"/ui/customers/{customer.id}/sso",
        data={
            "provider": "azure_ad", "display_name": "Customer AD", "issuer": "https://cust-idp.example.com",
            "client_id": "cid", "client_secret": "shh", "groups_claim_name": "groups", "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.expire_all()
    connections = sso_service.list_connections(db_session, tenant.id, customer_id=customer.id)
    assert len(connections) == 1
    assert connections[0].display_name == "Customer AD"


def test_customer_sso_edit_404s_for_connection_belonging_to_a_different_customer(client, db_session, tenant_factory):
    from openclear.services import customer_service

    tenant, _account, users = tenant_factory.make(slug="web-sso-cust-crosscheck")
    customer_a = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-A", name="A"))
    customer_b = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-B", name="B"))
    db_session.commit()
    connection_a, _group = _make_mapped_connection(db_session, tenant, customer_id=customer_a.id)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/customers/{customer_b.id}/sso/{connection_a.id}/edit", follow_redirects=False)

    assert resp.status_code == 404


def test_sso_callback_with_wrong_state_is_rejected(client, db_session, tenant_factory, monkeypatch):
    """The OAuth state binds the callback to the browser that started the login."""
    tenant, _account, _users = tenant_factory.make(slug="web-sso-bad-state")
    connection, _group = _make_mapped_connection(db_session, tenant)
    nonce = _start_sso(client, connection, tenant.id, monkeypatch)
    provider = FakeOidcProvider(issuer=connection.issuer, client_id=connection.client_id)
    id_token = provider.sign_id_token(sub="sub-1", email="newperson@example.com", groups=["openclear-users"], nonce=nonce)
    patch_provider(monkeypatch, provider, id_token)

    resp = client.get(f"/ui/login/sso/{connection.id}/callback", params={"code": "c", "state": "not-the-real-state"})

    assert resp.status_code == 401
    assert "state mismatch" in resp.text
    assert client.cookies.get("access_token") is None


def test_sso_start_with_unreachable_provider_shows_clean_error(client, db_session, tenant_factory, monkeypatch):
    import httpx

    import openclear.auth.oidc_service as oidc_service

    tenant, _account, _users = tenant_factory.make(slug="web-sso-unreachable")
    connection, _group = _make_mapped_connection(db_session, tenant)

    def _unreachable(issuer):
        raise httpx.ConnectError("Refusing to connect to idp: it resolves to a non-public address 10.0.0.5")

    monkeypatch.setattr(oidc_service, "_get_discovery_document", _unreachable)

    resp = client.get(f"/ui/login/sso/{connection.id}/start", params={"tenant_id": str(tenant.id)}, follow_redirects=False)

    assert resp.status_code == 502
    assert "Single sign-on couldn" in resp.text
    assert "10.0.0.5" not in resp.text  # network details stay in the server log


def _sso_form(client, issuer):
    return {
        "provider": list(SsoProvider)[0].value, "display_name": "Evil IdP", "issuer": issuer, "client_id": "cid",
        "client_secret": "secret", "groups_claim_name": "groups", "csrf_token": client.cookies.get("csrf_token"),
    }


def test_creating_sso_connection_with_internal_issuer_shows_form_error(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-sso-ssrf-create")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.post("/ui/admin/sso", data=_sso_form(client, "https://169.254.169.254"), follow_redirects=False)

    assert resp.status_code == 422
    assert "public address" in resp.text
    assert sso_service.list_connections(db_session, tenant.id) == []


def test_editing_sso_connection_to_internal_issuer_shows_form_error(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-sso-ssrf-edit")
    connection, _group = _make_mapped_connection(db_session, tenant)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.post(f"/ui/admin/sso/{connection.id}", data=_sso_form(client, "http://10.0.0.5:8080"), follow_redirects=False)

    assert resp.status_code == 303
    assert f"/ui/admin/sso/{connection.id}/edit?error=" in resp.headers["location"]
    db_session.expire_all()
    assert sso_service.get_connection(db_session, tenant.id, connection.id).issuer == "https://idp.example.com"


# --- People matched by the provider's subject (ASVS 10.5.2) ---


def _claims(email, subject):
    from openclear.auth.oidc_service import OidcClaims

    return OidcClaims(email=email, subject=subject, groups=["openclear-users"])


@pytest.mark.asvs("10.5.2")
def test_a_reassigned_email_is_refused(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-subject-reassigned")
    connection, _group = _make_mapped_connection(db_session, tenant)
    first = sso_service.complete_sso_login(db_session, tenant.id, connection, _claims("pat@acme.example.com", "sub-pat"))
    db_session.commit()
    assert first.outcome == "success"

    # The provider gives pat@ to someone new, with a new subject.
    newcomer = sso_service.complete_sso_login(db_session, tenant.id, connection, _claims("pat@acme.example.com", "sub-new-person"))
    assert newcomer.outcome == "identity_conflict"


def test_a_known_subject_with_a_changed_email_is_refused(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-subject-changed")
    connection, _group = _make_mapped_connection(db_session, tenant)
    assert sso_service.complete_sso_login(db_session, tenant.id, connection, _claims("lee@acme.example.com", "sub-lee")).outcome == "success"
    db_session.commit()
    moved = sso_service.complete_sso_login(db_session, tenant.id, connection, _claims("lee.smith@acme.example.com", "sub-lee"))
    assert moved.outcome == "identity_conflict"


def test_the_same_subject_and_email_sign_in_as_before(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-subject-same")
    connection, _group = _make_mapped_connection(db_session, tenant)
    for _ in range(2):
        assert sso_service.complete_sso_login(db_session, tenant.id, connection, _claims("kim@acme.example.com", "sub-kim")).outcome == "success"
        db_session.commit()


def test_without_a_subject_only_the_email_is_used(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-subject-none")
    connection, _group = _make_mapped_connection(db_session, tenant)
    for _ in range(2):  # e.g. a SAML transient NameID: nothing to compare
        assert sso_service.complete_sso_login(db_session, tenant.id, connection, _claims("ana@acme.example.com", "")).outcome == "success"
        db_session.commit()


# --- The provider must report MFA (OW-D25) ---


def _claims_with(authentication):
    from openclear.auth.oidc_service import OidcClaims

    return OidcClaims(email="mfa@acme.example.com", subject="sub-mfa", groups=["openclear-users"], authentication=authentication)


@pytest.mark.asvs("6.8.4")
def test_a_connection_can_require_the_provider_to_report_mfa(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-mfa-claim")
    connection, _group = _make_mapped_connection(db_session, tenant)
    connection.require_mfa_claim = True
    connection.mfa_values = "2, gold"
    db_session.commit()

    assert sso_service.complete_sso_login(db_session, tenant.id, connection, _claims_with(("pwd",))).outcome == "mfa_not_reported"
    for evidence in (("pwd", "mfa"), ("aal2",), ("http://schemas.microsoft.com/claims/multipleauthn",), ("2",)):
        result = sso_service.complete_sso_login(db_session, tenant.id, connection, _claims_with(evidence))
        assert result.outcome == "success", evidence
        db_session.commit()


def test_without_the_switch_the_provider_is_trusted(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sso-mfa-off")
    connection, _group = _make_mapped_connection(db_session, tenant)
    assert sso_service.complete_sso_login(db_session, tenant.id, connection, _claims_with(())).outcome == "success"


def test_turning_the_mfa_check_on_isn_t_a_loosening():
    from openclear.services.change_kinds import get

    kind = get("sso_connection.update")
    before = {"issuer": "x", "display_name": "A", "require_mfa_claim": False}
    assert not kind.loosens(before, {**before, "require_mfa_claim": True})
    assert kind.loosens({**before, "require_mfa_claim": True}, before)
