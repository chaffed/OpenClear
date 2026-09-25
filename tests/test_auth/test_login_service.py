# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import datetime, timedelta, timezone

from openclear.auth.login_service import PasswordLoginOutcome, authenticate_password
from openclear.config import get_settings
from openclear.domain.webauthn_credential import WebauthnCredential
from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.repositories.webauthn_credential_repo import WebauthnCredentialRepository
from openclear.services import sso_service, user_service
from tests.conftest import TenantFactory
from tests.test_web.test_web_sso_login import _make_mapped_connection


def test_password_login_blocked_when_tenant_requires_sso(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="login-svc-sso-blocked")
    _make_mapped_connection(db_session, tenant)
    sso_service.set_tenant_password_login_enabled(db_session, tenant.id, enabled=False)
    db_session.commit()

    result = authenticate_password(db_session, tenant.slug, users["admin"].email, TenantFactory.PASSWORD)

    assert result.outcome == PasswordLoginOutcome.SSO_REQUIRED


def test_require_webauthn_membership_bypasses_sso_required(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="login-svc-sso-override")
    _make_mapped_connection(db_session, tenant)
    sso_service.set_tenant_password_login_enabled(db_session, tenant.id, enabled=False)
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["admin"].id)[0]
    membership.require_webauthn = True
    db_session.commit()

    result = authenticate_password(db_session, tenant.slug, users["admin"].email, TenantFactory.PASSWORD)

    assert result.outcome == PasswordLoginOutcome.SUCCESS
    assert result.identity is not None
    assert result.identity.mfa_required is True
    assert result.identity.needs_webauthn_enrollment is True


def test_needs_webauthn_enrollment_false_once_key_registered(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="login-svc-sso-enrolled")
    _make_mapped_connection(db_session, tenant)
    sso_service.set_tenant_password_login_enabled(db_session, tenant.id, enabled=False)
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["admin"].id)[0]
    membership.require_webauthn = True
    WebauthnCredentialRepository(db_session, tenant.id).add(
        WebauthnCredential(user_id=users["admin"].id, credential_id=b"fake-cred-id", public_key=b"fake-public-key")
    )
    db_session.commit()

    result = authenticate_password(db_session, tenant.slug, users["admin"].email, TenantFactory.PASSWORD)

    assert result.outcome == PasswordLoginOutcome.SUCCESS
    assert result.identity is not None
    assert result.identity.mfa_required is True
    assert result.identity.needs_webauthn_enrollment is False


def test_ordinary_membership_unaffected_by_require_webauthn_default(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="login-svc-ordinary")

    result = authenticate_password(db_session, tenant.slug, users["admin"].email, TenantFactory.PASSWORD)

    assert result.outcome == PasswordLoginOutcome.SUCCESS
    assert result.identity is not None
    assert result.identity.mfa_required is False
    assert result.identity.needs_webauthn_enrollment is False


def test_account_locks_after_max_failed_attempts(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="login-svc-lockout")
    max_attempts = get_settings().login_max_failed_attempts

    for _ in range(max_attempts - 1):
        result = authenticate_password(db_session, tenant.slug, users["admin"].email, "wrong-password")
        assert result.outcome == PasswordLoginOutcome.INVALID_CREDENTIALS
    db_session.commit()

    result = authenticate_password(db_session, tenant.slug, users["admin"].email, "wrong-password")
    db_session.commit()
    assert result.outcome == PasswordLoginOutcome.LOCKED
    assert users["admin"].locked_until is not None

    from openclear.domain.notification import Notification, NotificationType

    notifs = (
        db_session.query(Notification)
        .filter(
            Notification.recipient_user_id == users["admin"].id, Notification.notification_type == NotificationType.ACCOUNT_LOCKED
        )
        .all()
    )
    assert len(notifs) == 1


def test_lockout_notification_fires_once_not_on_every_subsequent_rejected_attempt(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="login-svc-lockout-notify-once")
    max_attempts = get_settings().login_max_failed_attempts

    for _ in range(max_attempts):
        authenticate_password(db_session, tenant.slug, users["admin"].email, "wrong-password")
        db_session.commit()
    # Two more attempts against an already-locked account -- must not queue two more
    # lockout notifications on top of the first.
    for _ in range(2):
        result = authenticate_password(db_session, tenant.slug, users["admin"].email, "wrong-password")
        db_session.commit()
        assert result.outcome == PasswordLoginOutcome.LOCKED

    from openclear.domain.notification import Notification, NotificationType

    notifs = (
        db_session.query(Notification)
        .filter(
            Notification.recipient_user_id == users["admin"].id, Notification.notification_type == NotificationType.ACCOUNT_LOCKED
        )
        .all()
    )
    assert len(notifs) == 1


def test_locked_account_rejects_even_the_correct_password(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="login-svc-lockout-correct-pw")
    max_attempts = get_settings().login_max_failed_attempts
    for _ in range(max_attempts):
        authenticate_password(db_session, tenant.slug, users["admin"].email, "wrong-password")
    db_session.commit()

    result = authenticate_password(db_session, tenant.slug, users["admin"].email, TenantFactory.PASSWORD)

    assert result.outcome == PasswordLoginOutcome.LOCKED


def test_successful_login_resets_failed_attempt_counter(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="login-svc-lockout-reset")
    authenticate_password(db_session, tenant.slug, users["admin"].email, "wrong-password")
    db_session.commit()
    assert users["admin"].failed_login_attempts == 1

    result = authenticate_password(db_session, tenant.slug, users["admin"].email, TenantFactory.PASSWORD)

    assert result.outcome == PasswordLoginOutcome.SUCCESS
    assert users["admin"].failed_login_attempts == 0
    assert users["admin"].locked_until is None


def test_expired_lock_allows_login_again(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="login-svc-lockout-expired")
    users["admin"].locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    result = authenticate_password(db_session, tenant.slug, users["admin"].email, TenantFactory.PASSWORD)

    assert result.outcome == PasswordLoginOutcome.SUCCESS


def test_unlock_user_clears_lockout(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="login-svc-manual-unlock")
    max_attempts = get_settings().login_max_failed_attempts
    for _ in range(max_attempts):
        authenticate_password(db_session, tenant.slug, users["admin"].email, "wrong-password")
    db_session.commit()
    assert users["admin"].locked_until is not None

    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=users["admin"].id)[0]
    unlocked = user_service.unlock_user(db_session, tenant.id, membership.id)
    db_session.commit()

    assert unlocked is not None
    assert unlocked.failed_login_attempts == 0
    assert unlocked.locked_until is None

    result = authenticate_password(db_session, tenant.slug, users["admin"].email, TenantFactory.PASSWORD)
    assert result.outcome == PasswordLoginOutcome.SUCCESS
