# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Adding or removing a security key needs a recent sign-in, and is recorded and emailed
(ASVS 5.0 7.5.1, 6.3.7; auth/deps.py::recently_signed_in). The sign-in time travels with the
session through refresh and switch without being renewed by them."""

import time
import uuid

import pytest

from openclear.auth.security import create_session_tokens, decode_token
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.webauthn_credential import WebauthnCredential
from openclear.services import security_group_service, session_service


def _tokens(db_session, tenant, user, **claims):
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    return create_session_tokens(user_id=user.id, tenant_id=tenant.id, security_group_id=group.id, customer_id=None,
                                 token_version=user.token_version, signed_in_with="password", **claims)


def _key(db_session, tenant, user, nickname="Desk key"):
    row = WebauthnCredential(tenant_id=tenant.id, user_id=user.id, credential_id=uuid.uuid4().bytes, public_key=b"k",
                             sign_count=0, nickname=nickname)
    db_session.add(row)
    db_session.commit()
    return row


def _browser(client, tokens):
    client.get("/ui/login")
    for name in ("access_token", "refresh_token"):
        for cookie in [c for c in client.cookies.jar if c.name == name]:
            client.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
    client.cookies.set("access_token", tokens.access_token)
    client.cookies.set("refresh_token", tokens.refresh_token)
    return client.cookies.get("csrf_token")


STALE = int(time.time()) - 3600


@pytest.mark.asvs("7.5.1")
@pytest.mark.parametrize("authenticated_at", [STALE, None], ids=["an hour ago", "before 1.22"])
def test_a_stale_session_cant_change_keys_on_the_web(client, db_session, tenant_factory, authenticated_at):
    tenant, _account, users = tenant_factory.make(slug=f"key-stale-web-{'old' if authenticated_at is None else 'hour'}")
    admin = users["admin"]
    row = _key(db_session, tenant, admin)
    csrf = _browser(client, _tokens(db_session, tenant, admin, authenticated_at=authenticated_at))

    options = client.post("/ui/security/webauthn/register/options", headers={"X-CSRF-Token": csrf})
    assert options.status_code == 403 and "sign in again" in options.json()["error"]
    removed = client.post(f"/ui/security/webauthn/{row.id}/delete", data={"csrf_token": csrf}, follow_redirects=False)
    assert "sign+in+again" in removed.headers["location"]
    db_session.expire_all()
    assert db_session.get(WebauthnCredential, row.id) is not None


@pytest.mark.asvs("6.3.7")
def test_a_recent_session_removes_a_key_and_it_is_recorded_and_emailed(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="key-recent-web")
    admin = users["admin"]
    row = _key(db_session, tenant, admin, nickname="Old phone")
    csrf = _browser(client, _tokens(db_session, tenant, admin))  # just signed in

    removed = client.post(f"/ui/security/webauthn/{row.id}/delete", data={"csrf_token": csrf}, follow_redirects=False)

    assert "Sign+out+other+devices" in removed.headers["location"]
    assert db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id, action="user.security_key_removed").count() == 1
    note = db_session.query(Notification).filter_by(recipient_user_id=admin.id,
                                                    notification_type=NotificationType.SECURITY_KEY_CHANGED).one()
    assert "Old phone" in note.body and "removed" in note.body


def test_the_api_refuses_a_stale_session(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="key-stale-api")
    admin = users["admin"]
    row = _key(db_session, tenant, admin)
    headers = {"Authorization": f"Bearer {_tokens(db_session, tenant, admin, authenticated_at=STALE).access_token}"}

    assert client.post("/api/v1/auth/webauthn/register/options", headers=headers).status_code == 403
    assert client.delete(f"/api/v1/auth/webauthn/credentials/{row.id}", headers=headers).status_code == 403

    fresh = {"Authorization": f"Bearer {_tokens(db_session, tenant, admin).access_token}"}
    assert client.delete(f"/api/v1/auth/webauthn/credentials/{row.id}", headers=fresh).status_code == 204
    assert db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id, action="user.security_key_removed").count() == 1


def test_refresh_keeps_the_sign_in_time(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="key-refresh")
    tokens = _tokens(db_session, tenant, users["admin"], authenticated_at=STALE)
    refreshed, _tenant = session_service.refresh_session(db_session, tokens.refresh_token)
    assert decode_token(refreshed.access_token)["auth"] == STALE


def test_a_new_sign_in_records_its_time(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="key-new")
    before = int(time.time())
    assert decode_token(_tokens(db_session, tenant, users["admin"]).access_token)["auth"] >= before


# --- Issuer and audience on OpenClear's own tokens (ASVS 9.2.3, 9.2.4) ---


@pytest.mark.asvs("9.2.3")
def test_tokens_name_this_installation_and_their_purpose(db_session, tenant_factory, monkeypatch):
    import jwt

    from openclear.auth.security import PLATFORM_AUDIENCE, SESSION_AUDIENCE, token_issuer
    from openclear.config import get_settings

    tenant, _account, users = tenant_factory.make(slug="tok-iss-aud")
    token = _tokens(db_session, tenant, users["admin"]).access_token
    claims = decode_token(token)
    assert claims["iss"] == token_issuer() and claims["aud"] == SESSION_AUDIENCE

    with pytest.raises(jwt.InvalidAudienceError):  # a session token isn't a platform session
        decode_token(token, audience=PLATFORM_AUDIENCE)
    monkeypatch.setattr(get_settings(), "webauthn_origin", "https://another-installation.example")
    with pytest.raises(jwt.InvalidIssuerError):  # nor good at another installation with the same keys
        decode_token(token)


@pytest.mark.asvs("9.2.4")
def test_a_token_without_an_issuer_and_audience_is_refused():
    from datetime import datetime, timedelta, timezone

    import jwt

    from openclear.auth.keys import load_private_key
    from openclear.config import get_settings

    settings = get_settings()
    old = jwt.encode({"sub": "x", "type": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                     load_private_key(settings.jwt_private_key_path), algorithm=settings.jwt_algorithm)
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_token(old)
