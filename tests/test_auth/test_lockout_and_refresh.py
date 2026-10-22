# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Wrong passwords counted the same way everywhere, by the bank's policy; a limit on sign-in
attempts per address; refresh tokens used once (FULL_REVIEW_PLAN.md FR3: S1, S2)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from openclear.auth.login_service import LOCKED_UNTIL_UNLOCKED, PasswordLoginOutcome, authenticate_password
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.revoked_session import RevokedSession
from openclear.domain.tenant import Tenant
from openclear.services import session_service, user_service
from tests.conftest import TenantFactory


def _locked_emails(db_session, user) -> int:
    return len(db_session.execute(select(Notification).where(
        Notification.recipient_user_id == user.id, Notification.notification_type == NotificationType.ACCOUNT_LOCKED)).scalars().all())


@pytest.mark.asvs("6.3.1")
def test_the_password_form_follows_the_banks_until_unlocked_policy(db_session, tenant_factory):
    """Changing one's password with a wrong current one counts by the bank's rule, as signing in
    does; it used to use the installation's 15 minutes whatever the bank chose."""
    tenant, _account, users = tenant_factory.make(slug="lock-policy")
    bank = db_session.get(Tenant, tenant.id)
    bank.login_max_failed_attempts, bank.lockout_until_unlocked = 2, True
    db_session.commit()
    for _ in range(2):
        with pytest.raises(user_service.PasswordChangeError):
            user_service.change_own_password(db_session, tenant.id, users["viewer"].id, current_password="wrong",
                                             new_password="A-new-long-passphrase-9")
        db_session.commit()
    db_session.refresh(users["viewer"])
    stored = users["viewer"].locked_until
    assert (stored if stored.tzinfo else stored.replace(tzinfo=timezone.utc)) == LOCKED_UNTIL_UNLOCKED
    assert _locked_emails(db_session, users["viewer"]) == 1


@pytest.mark.asvs("6.3.1")
def test_after_a_lock_runs_out_the_next_wrong_password_locks_again(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="lock-again")
    db_session.get(Tenant, tenant.id).login_max_failed_attempts = 3
    db_session.commit()
    for _ in range(3):
        authenticate_password(db_session, tenant.slug, users["viewer"].email, "wrong")
        db_session.commit()
    users["viewer"].locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)  # it ran out
    db_session.commit()
    result = authenticate_password(db_session, tenant.slug, users["viewer"].email, "wrong")
    db_session.commit()
    assert result.outcome == PasswordLoginOutcome.LOCKED
    assert _locked_emails(db_session, users["viewer"]) == 2
    assert authenticate_password(db_session, tenant.slug, users["viewer"].email, TenantFactory.PASSWORD).outcome == PasswordLoginOutcome.LOCKED


@pytest.mark.asvs("6.3.1")
def test_sign_in_attempts_are_limited_per_address(client, tenant_factory, monkeypatch):
    from openclear.config import get_settings

    tenant, _account, users = tenant_factory.make(slug="lock-rate")
    monkeypatch.setenv("OPENCLEAR_LOGIN_ATTEMPTS_PER_MINUTE", "3")
    get_settings.cache_clear()
    answers = [client.post("/api/v1/auth/login", json={"tenant_slug": tenant.slug, "email": f"nobody{n}@example.com",
                                                      "password": "guess"}).status_code for n in range(4)]
    assert answers == [401, 401, 401, 429]


def _refresh_token(client, tenant, user) -> str:
    resp = client.post("/api/v1/auth/login", json={"tenant_slug": tenant.slug, "email": user.email, "password": TenantFactory.PASSWORD})
    return resp.json()["refresh_token"]


def test_a_refresh_token_is_used_once(client, db_session, tenant_factory, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="refresh-once")
    first = _refresh_token(client, tenant, users["viewer"])
    renewed = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert renewed.status_code == 200
    second = renewed.json()["refresh_token"]

    # The same token again a moment later: two tabs refreshing together, so it's still accepted.
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": first}).status_code == 200

    # Later, it's a copy: refused, and the session is ended, so the newer token stops working too.
    monkeypatch.setattr(session_service, "REUSE_GRACE", timedelta(0))
    reused = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert reused.status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": second}).status_code == 401
    from openclear.domain.security_event import SecurityEvent

    assert db_session.execute(select(SecurityEvent).where(SecurityEvent.name == "auth.session.reuse_detected")).scalars().first()


def test_retired_refresh_tokens_are_kept_only_until_they_expire(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="refresh-kept")
    token = _refresh_token(client, tenant, users["viewer"])
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": token}).status_code == 200
    from openclear.auth.security import decode_token

    claims = decode_token(token)
    row = db_session.get(RevokedSession, __import__("uuid").UUID(claims["rid"]))
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    assert expires == datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
