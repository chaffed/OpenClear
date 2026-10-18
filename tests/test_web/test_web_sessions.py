# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Session renewal, the server-enforced idle timeout, and server-side revocation — see
services/session_service.py, web/routers/session.py, and auth/security.py::
create_session_tokens (FIX_PLAN.md Phase 3)."""

import uuid
import pytest
from datetime import datetime, timedelta, timezone

import webauthn
from fastapi.testclient import TestClient

from openclear.auth.security import create_session_tokens, create_token, decode_token
from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import customer_service, security_group_service, user_service
from openclear.services.security_group_service import SecurityGroupInput
from tests.conftest import TenantFactory, login_headers
from tests.test_auth.webauthn_helpers import FakeAuthenticator

pytestmark = pytest.mark.usefixtures("email_configured")  # admin password resets email a link


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    return client.post(
        "/ui/login",
        data={"tenant_slug": tenant_slug, "email": email, "password": password,
              "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"},
        follow_redirects=False,
    )


def _clone(client) -> TestClient:
    """A second browser holding a copy of `client`'s cookies — what an attacker with
    stolen cookies (or the same user on another device) looks like to the server."""
    other = TestClient(client.app)
    for cookie in client.cookies.jar:
        other.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
    return other


def _signed_in(client) -> bool:
    return client.get("/ui/exceptions", follow_redirects=False).status_code == 200


def _membership(db_session, user, tenant):
    return db_session.query(TenantMembership).filter_by(user_id=user.id, tenant_id=tenant.id).one()


def _set_session_cookies(client, *, user, tenant, membership, access_expires_at, session_expires_at, session_id=None):
    """Plants a hand-built session in `client` so tests can put it at any point in its
    lifetime without waiting (or a time-freezing library)."""
    session_id = session_id or uuid.uuid4()
    common = dict(
        user_id=user.id, tenant_id=tenant.id, security_group_id=membership.security_group_id,
        customer_id=membership.customer_id, token_version=user.token_version, session_id=session_id,
        session_expires_at=session_expires_at,
    )
    access = create_token(token_type="access", expires_at=access_expires_at, **common)
    refresh = create_token(token_type="refresh", expires_at=session_expires_at, **common)
    client.get("/ui/login")  # a csrf_token cookie
    # Same domain the server's own Set-Cookie uses, so a later server-issued cookie
    # replaces these instead of sitting alongside them in the jar.
    domain = next(c.domain for c in client.cookies.jar if c.name == "csrf_token")
    client.cookies.set("access_token", access, domain=domain, path="/")
    client.cookies.set("refresh_token", refresh, domain=domain, path="/ui/auth")
    return session_id


def _refresh(client):
    return client.post("/ui/auth/refresh", headers={"X-CSRF-Token": client.cookies.get("csrf_token")})


# --- Logout and organization switch revoke the session server-side ---


def test_logout_revokes_the_session_for_copied_cookies(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-logout")
    _login(client, tenant.slug, users["viewer"].email)
    stolen = _clone(client)
    assert _signed_in(stolen)

    client.post("/ui/logout", data={"csrf_token": client.cookies.get("csrf_token")})

    denied = stolen.get("/ui/exceptions", follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"].startswith("/ui/login")


def test_logout_only_ends_that_session(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-logout-one")
    other_device = TestClient(client.app)
    _login(other_device, tenant.slug, users["viewer"].email)
    _login(client, tenant.slug, users["viewer"].email)

    client.post("/ui/logout", data={"csrf_token": client.cookies.get("csrf_token")})

    assert _signed_in(other_device)


def test_logout_with_an_expired_access_token_still_revokes_the_session(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-logout-expired")
    viewer = users["viewer"]
    now = datetime.now(timezone.utc)
    _set_session_cookies(
        client, user=viewer, tenant=tenant, membership=_membership(db_session, viewer, tenant),
        access_expires_at=now - timedelta(seconds=30), session_expires_at=now + timedelta(hours=1),
    )
    stolen = _clone(client)

    client.post("/ui/logout", data={"csrf_token": client.cookies.get("csrf_token")})

    assert _refresh(stolen).status_code == 401  # would otherwise renew: still within the grace period


def test_switching_organization_ends_the_old_session(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-switch-a")
    other_tenant, _account2, _users2 = tenant_factory.make(slug="sess-switch-b")
    viewer = users["viewer"]
    group = security_group_service.list_security_groups(db_session, other_tenant.id)[0]
    target = user_service.confirm_cross_tenant_membership(db_session, other_tenant.id, email=viewer.email, security_group_id=group.id)
    db_session.commit()
    _login(client, tenant.slug, viewer.email)
    old_cookies = _clone(client)

    resp = client.post(
        "/ui/switch-tenant", data={"membership_id": str(target.id), "csrf_token": client.cookies.get("csrf_token")},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert _signed_in(client)
    assert not _signed_in(old_cookies)


# --- Signing out everywhere (token_version) ---


def test_password_change_signs_out_other_sessions_but_not_this_one(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-pw-change")
    other_device = TestClient(client.app)
    _login(other_device, tenant.slug, users["viewer"].email)
    _login(client, tenant.slug, users["viewer"].email)

    client.post(
        "/ui/security/password",
        data={"current_password": TenantFactory.PASSWORD, "new_password": "A-new-password-123",
              "confirm_password": "A-new-password-123", "csrf_token": client.cookies.get("csrf_token")},
    )

    assert _signed_in(client)
    assert not _signed_in(other_device)


def test_admin_password_reset_signs_the_user_out_everywhere(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-reset")
    viewer_browser = TestClient(client.app)
    _login(viewer_browser, tenant.slug, users["viewer"].email)
    _login(client, tenant.slug, users["admin"].email)

    client.post(
        f"/ui/users/{_membership(db_session, users['viewer'], tenant).id}/reset-password",
        data={"csrf_token": client.cookies.get("csrf_token")},
    )

    assert not _signed_in(viewer_browser)


def test_admin_can_sign_a_user_out_everywhere(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-admin-signout")
    viewer = users["viewer"]
    viewer_browser = TestClient(client.app)
    _login(viewer_browser, tenant.slug, viewer.email)
    api_headers = login_headers(TestClient(client.app), tenant.slug, viewer.email)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        f"/ui/users/{_membership(db_session, viewer, tenant).id}/sign-out",
        data={"csrf_token": client.cookies.get("csrf_token")}, follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "signed+out" in resp.headers["location"] or "signed%20out" in resp.headers["location"]
    assert not _signed_in(viewer_browser)
    assert client.get("/api/v1/exceptions", headers=api_headers).status_code == 401
    assert _signed_in(client)
    assert db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id, action="user.sign_out_everywhere").count() == 1


def test_admin_sign_out_rejects_own_and_foreign_memberships(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-admin-signout-guard")
    other_tenant, _account2, other_users = tenant_factory.make(slug="sess-admin-signout-guard-2")
    other_browser = TestClient(client.app)
    _login(other_browser, other_tenant.slug, other_users["viewer"].email)
    _login(client, tenant.slug, users["admin"].email)
    csrf = {"csrf_token": client.cookies.get("csrf_token")}

    own = client.post(f"/ui/users/{_membership(db_session, users['admin'], tenant).id}/sign-out", data=csrf, follow_redirects=False)
    foreign = client.post(
        f"/ui/users/{_membership(db_session, other_users['viewer'], other_tenant).id}/sign-out", data=csrf, follow_redirects=False
    )

    assert "error" in own.headers["location"] and "error" in foreign.headers["location"]
    assert _signed_in(client)
    assert _signed_in(other_browser)


def test_user_can_sign_out_their_other_devices(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-self-signout")
    other_device = TestClient(client.app)
    _login(other_device, tenant.slug, users["viewer"].email)
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.post("/ui/security/sign-out-others", data={"csrf_token": client.cookies.get("csrf_token")}, follow_redirects=False)

    assert resp.status_code == 303
    assert _signed_in(client)
    assert not _signed_in(other_device)


def test_api_refresh_is_rejected_after_sign_out_everywhere(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-api-refresh-revoked")
    viewer = users["viewer"]
    login = client.post("/api/v1/auth/login", json={"tenant_slug": tenant.slug, "email": viewer.email, "password": TenantFactory.PASSWORD})
    refresh_token = login.json()["refresh_token"]
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token}).status_code == 200

    user = db_session.get(User, viewer.id)
    user.token_version += 1
    db_session.commit()

    assert client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token}).status_code == 401


def test_api_refresh_keeps_the_session_and_its_maximum_length(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-api-refresh")
    login = client.post(
        "/api/v1/auth/login", json={"tenant_slug": tenant.slug, "email": users["viewer"].email, "password": TenantFactory.PASSWORD}
    ).json()
    before = decode_token(login["refresh_token"])

    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}).json()
    after = decode_token(refreshed["refresh_token"])

    assert after["sid"] == before["sid"]
    assert after["exp"] == before["exp"]


def test_tokens_minted_before_session_claims_still_work(client, db_session, tenant_factory):
    """No forced sign-out on deploy: a token without tv/sid counts as version 0 and is
    simply not individually revocable."""
    import jwt

    from openclear.auth.keys import load_private_key
    from openclear.auth.security import SESSION_AUDIENCE, token_issuer

    tenant, _account, users = tenant_factory.make(slug="sess-legacy-token")
    viewer = users["viewer"]
    membership = _membership(db_session, viewer, tenant)
    now = datetime.now(timezone.utc)
    settings = get_settings()
    legacy = jwt.encode(
        {"sub": str(viewer.id), "tenant_id": str(tenant.id), "security_group_id": str(membership.security_group_id),
         "type": "access", "iat": now, "exp": now + timedelta(minutes=5),  # no tv, sid, or sxp
         "iss": token_issuer(), "aud": SESSION_AUDIENCE},  # required since 1.23
        load_private_key(settings.jwt_private_key_path),
        algorithm=settings.jwt_algorithm,
    )

    assert client.get("/api/v1/exceptions", headers={"Authorization": f"Bearer {legacy}"}).status_code == 200


# --- Renewal and the idle timeout ---


def test_refresh_extends_the_idle_timeout_but_not_the_session(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-refresh")
    viewer = users["viewer"]
    now = datetime.now(timezone.utc)
    session_end = now + timedelta(hours=2)
    _set_session_cookies(
        client, user=viewer, tenant=tenant, membership=_membership(db_session, viewer, tenant),
        access_expires_at=now + timedelta(minutes=3), session_expires_at=session_end,
    )

    resp = _refresh(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["access_expires_at"] > int((now + timedelta(minutes=20)).timestamp())
    assert body["session_expires_at"] == int(session_end.timestamp())
    assert _signed_in(client)


def test_refresh_never_goes_past_the_maximum_session_length(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-refresh-cap")
    viewer = users["viewer"]
    now = datetime.now(timezone.utc)
    session_end = now + timedelta(minutes=4)
    _set_session_cookies(
        client, user=viewer, tenant=tenant, membership=_membership(db_session, viewer, tenant),
        access_expires_at=now + timedelta(minutes=1), session_expires_at=session_end,
    )

    body = _refresh(client).json()

    assert body["access_expires_at"] == body["session_expires_at"] == int(session_end.timestamp())


def test_refresh_requires_the_csrf_header(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-refresh-csrf")
    _login(client, tenant.slug, users["viewer"].email)

    assert client.post("/ui/auth/refresh").status_code == 403


def test_idle_session_cannot_be_renewed(client, db_session, tenant_factory):
    """The idle timeout is enforced server-side: a still-valid refresh token must not
    revive a session whose access token expired well before."""
    tenant, _account, users = tenant_factory.make(slug="sess-idle")
    viewer = users["viewer"]
    now = datetime.now(timezone.utc)
    _set_session_cookies(
        client, user=viewer, tenant=tenant, membership=_membership(db_session, viewer, tenant),
        access_expires_at=now - timedelta(minutes=10), session_expires_at=now + timedelta(hours=5),
    )

    background = _clone(client)

    # A page load goes through resume, which refuses and sends the user to sign in
    # (then back to this page).
    page = client.get("/ui/exceptions", follow_redirects=False)
    assert page.headers["location"].startswith("/ui/auth/resume")
    resume = client.get(page.headers["location"], follow_redirects=False)
    assert resume.headers["location"] == "/ui/login?next=/ui/exceptions"
    # The page script's background renewal is refused too.
    assert _refresh(background).status_code == 401


def test_recently_expired_page_request_resumes_where_the_user_was(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-resume")
    viewer = users["viewer"]
    now = datetime.now(timezone.utc)
    _set_session_cookies(
        client, user=viewer, tenant=tenant, membership=_membership(db_session, viewer, tenant),
        access_expires_at=now - timedelta(seconds=20), session_expires_at=now + timedelta(hours=5),
    )

    page = client.get("/ui/exceptions?status=open", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/ui/auth/resume?next=/ui/exceptions%3Fstatus%3Dopen"

    resume = client.get(page.headers["location"], follow_redirects=False)
    assert resume.status_code == 303
    assert resume.headers["location"] == "/ui/exceptions?status=open"
    assert _signed_in(client)


def test_expired_form_post_returns_to_the_form_page_not_the_post_url(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-expired-post")
    viewer = users["viewer"]
    now = datetime.now(timezone.utc)
    _set_session_cookies(
        client, user=viewer, tenant=tenant, membership=_membership(db_session, viewer, tenant),
        access_expires_at=now - timedelta(hours=1), session_expires_at=now + timedelta(hours=5),
    )

    resp = client.post(
        "/ui/security/sign-out-others",
        data={"csrf_token": client.cookies.get("csrf_token")},
        headers={"Referer": "http://testserver/ui/security?tab=keys"},
        follow_redirects=False,
    )

    assert resp.headers["location"] == "/ui/auth/resume?next=/ui/security%3Ftab%3Dkeys"


def test_cross_site_referer_is_never_used_as_the_return_page(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-expired-post-xsite")
    viewer = users["viewer"]
    now = datetime.now(timezone.utc)
    _set_session_cookies(
        client, user=viewer, tenant=tenant, membership=_membership(db_session, viewer, tenant),
        access_expires_at=now - timedelta(hours=1), session_expires_at=now + timedelta(hours=5),
    )

    resp = client.post(
        "/ui/security/sign-out-others",
        data={"csrf_token": client.cookies.get("csrf_token")},
        headers={"Referer": "https://evil.example/ui/security"},
        follow_redirects=False,
    )

    assert resp.headers["location"] == "/ui/auth/resume?next=/ui/"


# --- Pages, cookies, settings ---


def test_signed_in_pages_publish_session_times_and_the_warning_dialog(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-meta")
    assert "openclear-access-expires" not in client.get("/ui/login").text
    _login(client, tenant.slug, users["viewer"].email)

    page = client.get("/ui/exceptions").text

    assert 'name="openclear-access-expires"' in page
    assert 'name="openclear-session-expires"' in page
    assert 'id="session-dialog"' in page
    assert "/static/js/session.js" in page


def test_csrf_cookie_is_a_browser_session_cookie(client):
    resp = client.get("/ui/login")

    csrf_header = next(h for h in resp.headers.get_list("set-cookie") if h.startswith("csrf_token="))
    assert "max-age" not in csrf_header.lower()
    assert "expires" not in csrf_header.lower()


def test_signed_in_responses_are_not_cached(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-no-store")
    _login(client, tenant.slug, users["viewer"].email)

    assert client.get("/ui/exceptions").headers["cache-control"] == "no-store"
    assert client.get("/api/v1/exceptions", headers=login_headers(client, tenant.slug, users["viewer"].email)).headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/static/js/app.js").headers or client.get("/static/js/app.js").headers["cache-control"] != "no-store"


def test_idle_timeout_cannot_exceed_maximum_session_length(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-settings")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/settings/session-timeout",
        data={"access_token_expire_minutes": "120", "refresh_token_expire_minutes": "60",
              "csrf_token": client.cookies.get("csrf_token")},
    )

    assert resp.status_code == 422
    assert "can&#39;t be longer than the maximum session length" in resp.text or "can't be longer" in resp.text


def test_tenant_session_timeouts_shape_the_session(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-tenant-timeouts")
    tenant.access_token_expire_minutes = 10
    tenant.refresh_token_expire_minutes = 90
    db_session.commit()

    _login(client, tenant.slug, users["viewer"].email)
    claims = decode_token(client.cookies.get("access_token"))
    now = int(datetime.now(timezone.utc).timestamp())

    assert 9 * 60 <= claims["exp"] - now <= 10 * 60  # idle sign-out
    assert 89 * 60 <= claims["sxp"] - now <= 90 * 60  # maximum session length


# --- Fixed along the way: API WebAuthn sign-in lost the customer scope ---


def test_api_webauthn_login_keeps_the_customer_scope(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="sess-api-webauthn-customer")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C9", name="Nine"))
    group = security_group_service.create_security_group(
        db_session, tenant.id, SecurityGroupInput(name="Customer Viewer", permissions=["issued_item:read"])
    )
    user_service.create_user_with_membership(
        db_session, tenant.id, email="scoped@example.com", password=TenantFactory.PASSWORD,
        security_group_id=group.id, customer_id=customer.id,
    )
    db_session.commit()
    fake = FakeAuthenticator(get_settings().webauthn_rp_id, get_settings().webauthn_origin)
    headers = login_headers(client, tenant.slug, "scoped@example.com")
    options = webauthn.helpers.parse_registration_options_json(
        client.post("/api/v1/auth/webauthn/register/options", headers=headers).text
    )
    client.post(
        "/api/v1/auth/webauthn/register/verify", headers=headers,
        json={"credential": fake.create_registration_credential(options.challenge), "nickname": "Key"},
    )

    mfa_token = client.post(
        "/api/v1/auth/login", json={"tenant_slug": tenant.slug, "email": "scoped@example.com", "password": TenantFactory.PASSWORD}
    ).json()["mfa_token"]
    mfa_headers = {"Authorization": f"Bearer {mfa_token}"}
    auth_options = webauthn.helpers.parse_authentication_options_json(
        client.post("/api/v1/auth/webauthn/login/options", headers=mfa_headers).text
    )
    tokens = client.post(
        "/api/v1/auth/webauthn/login/verify", headers=mfa_headers,
        json={"credential": fake.create_authentication_credential(auth_options.challenge)},
    ).json()

    assert decode_token(tokens["access_token"])["customer_id"] == str(customer.id)
    assert client.get("/api/v1/issued-items", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code == 200


def test_create_session_tokens_share_one_session(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-unit")
    viewer = users["viewer"]
    membership = _membership(db_session, viewer, tenant)

    tokens = create_session_tokens(
        user_id=viewer.id, tenant_id=tenant.id, security_group_id=membership.security_group_id,
        customer_id=None, token_version=3,
    )
    access, refresh = decode_token(tokens.access_token), decode_token(tokens.refresh_token)

    assert access["sid"] == refresh["sid"] == str(tokens.session_id)
    assert access["tv"] == refresh["tv"] == 3
    assert access["type"] == "access" and refresh["type"] == "refresh"
    assert refresh["exp"] == access["sxp"] == int(tokens.session_expires_at.timestamp())


@pytest.mark.asvs("14.3.1")
def test_ending_a_session_clears_the_browser_s_cache_and_storage(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="sess-clear-site")
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    out = client.post("/ui/logout", data={"csrf_token": client.cookies.get("csrf_token")}, follow_redirects=False)
    assert out.headers["clear-site-data"] == '"cache", "storage"'
    assert client.get("/ui/embed/ended").headers["clear-site-data"] == '"cache", "storage"'
