# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""An administrator's password reset, by one-time link (OWASP_PLAN.md OW3a; ASVS 5.0 6.4.6, 6.4.1).

The administrator starts it; the person finishes it. Starting it makes the old password stop
working, signs the person out everywhere, clears a lockout, and emails them a link that works
once and expires (Settings.password_reset_link_hours). The administrator never sees or chooses
a password. The link is `<bank id hex>.<secret>`, like an invitation's, so the signed-out page
can bind to the right bank under row-level security; only the secret's SHA-256 is stored."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from openclear.auth.password_policy import validate_password
from openclear.auth.security import hash_password
from openclear.config import get_settings
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.password_reset import PasswordReset
from openclear.domain.tenant import Tenant
from openclear.domain.user import User


class ResetError(ValueError):
    """Why a reset can't be started or finished, for the page to show."""


@dataclass(frozen=True)
class OpenReset:
    reset: PasswordReset
    user: User
    tenant: Tenant


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start(session: Session, tenant: Tenant, user: User, *, requested_by_user_id: uuid.UUID, base_url: str) -> None:
    """The caller has already decided this administrator may reset this person
    (user_service.admin_reset_password) and records the audited action."""
    from openclear.services import notification_service, session_service

    if not notification_service.email_configured():
        raise ResetError("Email isn't set up for this installation, so a reset link can't be sent. "
                         "Ask your service provider to configure email.")
    hours = get_settings().password_reset_link_hours
    token = f"{tenant.id.hex}.{secrets.token_urlsafe(32)}"
    session.execute(delete(PasswordReset).where(PasswordReset.tenant_id == tenant.id, PasswordReset.user_id == user.id,
                                                PasswordReset.used_at.is_(None)))
    session.add(PasswordReset(tenant_id=tenant.id, user_id=user.id, token_hash=_hash(token),
                              requested_by_user_id=requested_by_user_id, expires_at=_now() + timedelta(hours=hours)))
    # The old password stops working now: the reset may be because it leaked.
    user.hashed_password = hash_password(secrets.token_urlsafe(48))
    user.must_change_password = False
    user.failed_login_attempts = 0
    user.locked_until = None
    session_service.revoke_all_sessions(session, user)
    notification_service.notify_password_reset_link(
        session, user, link=f"{base_url.rstrip('/')}/ui/password-reset/{token}", hours=hours, bank_name=tenant.name,
    )
    session.flush()


def open_reset(session: Session, token: str) -> OpenReset | None:
    """The reset behind a link, if it can still be used. Binds the session to its bank."""
    tenant_hex, _, _secret = (token or "").partition(".")
    try:
        tenant_id = uuid.UUID(hex=tenant_hex)
    except ValueError:
        return None
    tenant = session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        return None
    bind_session_to_tenant(session, tenant_id)
    reset = session.execute(select(PasswordReset).where(PasswordReset.token_hash == _hash(token))).scalar_one_or_none()
    if reset is None or reset.tenant_id != tenant_id or reset.used_at or _aware(reset.expires_at) <= _now():
        return None
    user = session.get(User, reset.user_id)
    if user is None or not user.is_active:
        return None
    return OpenReset(reset, user, tenant)


def complete(session: Session, opened: OpenReset, new_password: str) -> User:
    """Sets the new password against every policy the person is subject to. The caller
    records the audited action and commits."""
    from openclear.services import notification_service, session_service, user_service

    user = opened.user
    policy = user_service.password_policy_for_user(session, user.id)
    if policy is not None:
        try:
            validate_password(new_password, policy, context=user_service.password_context_for_user(session, user))
        except ValueError as exc:
            raise ResetError(str(exc)) from None
    user.hashed_password = hash_password(new_password)
    user.must_change_password = False
    opened.reset.used_at = _now()
    session_service.revoke_all_sessions(session, user)
    notification_service.notify_password_changed(session, user, reset_by_admin=True)
    session.flush()
    return user
