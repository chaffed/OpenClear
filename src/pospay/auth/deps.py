# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from pospay.auth.permissions import CUSTOMER_SCOPE_MASKED_PERMISSIONS
from pospay.auth.security import decode_token
from pospay.db.session import get_db
from pospay.db.tenancy import TenantContext, bind_session_to_tenant
from pospay.domain.customer import Customer
from pospay.domain.exception_item import ExceptionItem, ExceptionStatus
from pospay.domain.security_group import SecurityGroup
from pospay.domain.tenant_membership import TenantMembership
from pospay.domain.user import User
from pospay.services import session_service
from pospay.services.tenant_service import get_tenant_branding_by_id
from pospay.web.demo_guard import DEMO_LOCKED_MESSAGE, is_locked_in_demo

_bearer_scheme = HTTPBearer(auto_error=True)


class WrongTokenType(Exception):
    """Raised by decode_and_build_context when a token of the wrong `type` claim is
    presented (e.g. an mfa_pending token where an access token is required)."""


class AccessRevoked(Exception):
    """Raised by decode_and_build_context when the token is well-formed and unexpired but
    the access it once granted no longer exists: the user's global account was
    deactivated, their membership in this specific tenant was deactivated, or the
    security group it points at no longer exists. Treated identically to an invalid
    token by every caller — this is what makes deactivating a user/membership take effect
    immediately rather than waiting for the token to expire."""


def _pending_approval_count(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, user_id: uuid.UUID) -> int:
    stmt = select(func.count(ExceptionItem.id)).where(
        ExceptionItem.tenant_id == tenant_id,
        ExceptionItem.status == ExceptionStatus.PENDING_APPROVAL,
        or_(ExceptionItem.recommended_by_user_id.is_(None), ExceptionItem.recommended_by_user_id != user_id),
    )
    if customer_id is not None:
        stmt = stmt.where(ExceptionItem.customer_id == customer_id)
    return db.execute(stmt).scalar_one()


def decode_and_build_context(token: str, db: Session, *, expected_type: str) -> TenantContext:
    """Shared by every auth channel that needs to turn a raw JWT string into a
    TenantContext: the JSON API's Authorization-header dependencies below, and
    web/deps.py's cookie-based equivalent. Keeping this in one place means the two
    channels can never drift on claim validation — only on WHERE the token comes from.

    Deliberately does NOT catch jwt's exceptions itself: jwt.ExpiredSignatureError vs
    jwt.InvalidTokenError vs WrongTokenType are meaningfully different outcomes to a
    caller (e.g. the web layer treats "expired" as refreshable but "invalid" as an
    immediate redirect-to-login), so translation is left to each call site.

    Permissions are resolved fresh from the SecurityGroup row here, on every request,
    rather than being baked into the token — see auth/security.py::create_token — and the
    user/membership are re-checked for is_active here too, so editing a group's
    permissions or deactivating a user/membership takes effect on the very next request."""
    payload = decode_token(token)  # may raise jwt.ExpiredSignatureError / jwt.InvalidTokenError

    if payload.get("type") != expected_type:
        raise WrongTokenType(f"Expected a {expected_type!r} token, got {payload.get('type')!r}")

    user_id = uuid.UUID(payload["sub"])
    tenant_id = uuid.UUID(payload["tenant_id"])
    security_group_id = uuid.UUID(payload["security_group_id"])
    customer_id = uuid.UUID(payload["customer_id"]) if payload.get("customer_id") else None
    # Defense-in-depth for Postgres RLS (see db/tenancy.py::bind_session_to_tenant): the
    # lookups below read RLS-protected tables, and every later transaction on this
    # request's session (including any after a commit) runs as this tenant too. The
    # tenant comes from the token's verified signature. FastAPI shares one `db` across a
    # request's dependencies and handler.
    bind_session_to_tenant(db, tenant_id, user_id=user_id)

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise AccessRevoked("User account is no longer active")
    # Logged out (this session's sid revoked) or signed out everywhere (token_version
    # bumped) since this token was minted — see services/session_service.py.
    if not session_service.claims_are_current(db, payload, user):
        raise AccessRevoked("Session was signed out")

    # customer_id is part of the lookup (not just user_id/tenant_id) because one user can
    # now hold several memberships in the same tenant — one per customer, or a tenant-wide
    # one plus per-customer overrides (domain/tenant_membership.py). `== customer_id` here
    # correctly becomes `IS NULL` when customer_id is None (SQLAlchemy's usual behavior),
    # matching a tenant-wide membership.
    membership = db.execute(
        select(TenantMembership).where(
            TenantMembership.user_id == user_id,
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.customer_id == customer_id,
        )
    ).scalar_one_or_none()
    if membership is None or not membership.is_active:
        raise AccessRevoked("Membership in this organization is no longer active")

    # The membership's CURRENT group, not the token's security_group_id claim — otherwise
    # reassigning a user to a less-privileged group (e.g. Admin -> Viewer) would leave the
    # old group's permissions active until this token expired. The claim is only a hint
    # of what the group was at mint time; everything downstream (including routes that
    # re-mint tokens from ctx.security_group_id, e.g. the WebAuthn login tail) sees the
    # current one.
    security_group_id = membership.security_group_id
    group = db.get(SecurityGroup, security_group_id)
    if group is None or group.tenant_id != tenant_id:
        raise AccessRevoked("Security group no longer exists")

    branding = get_tenant_branding_by_id(db, tenant_id)
    if branding is None:
        raise AccessRevoked("Tenant no longer exists")

    permissions = frozenset(group.permissions)
    customer_name: str | None = None
    customer_banner_message: str | None = None
    if customer_id is not None:
        # Masked regardless of what the security group nominally contains — see
        # auth/permissions.py::CUSTOMER_SCOPE_MASKED_PERMISSIONS.
        permissions = permissions - CUSTOMER_SCOPE_MASKED_PERMISSIONS
        customer = db.get(Customer, customer_id)
        if customer is None or not customer.is_active:
            raise AccessRevoked("Customer no longer exists")
        customer_name = customer.name
        customer_banner_message = customer.banner_message

    ctx = TenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        security_group_id=security_group_id,
        permissions=permissions,
        tenant_slug=branding.slug,
        tenant_name=branding.name,
        accent_color=branding.accent_color,
        has_logo=branding.has_logo,
        has_favicon=branding.has_favicon,
        customer_id=customer_id,
        customer_name=customer_name,
        access_token_expire_minutes=branding.access_token_expire_minutes,
        refresh_token_expire_minutes=branding.refresh_token_expire_minutes,
        support_email=branding.support_email,
        support_phone=branding.support_phone,
        website=branding.website,
        address_line1=branding.address_line1,
        address_line2=branding.address_line2,
        city=branding.city,
        state=branding.state,
        postal_code=branding.postal_code,
        banner_message=branding.banner_message,
        is_demo=branding.is_demo,
        customer_banner_message=customer_banner_message,
        must_change_password=user.must_change_password,
        session_id=uuid.UUID(payload["sid"]) if payload.get("sid") else None,
        access_expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        session_expires_at=datetime.fromtimestamp(payload["sxp"], tz=timezone.utc) if payload.get("sxp") else None,
        pending_approval_count=_pending_approval_count(db, tenant_id, customer_id, user_id)
        if "exception:decide" in permissions
        else 0,
    )

    return ctx


def _header_context(credentials: HTTPAuthorizationCredentials, db: Session, *, expected_type: str) -> TenantContext:
    try:
        return decode_and_build_context(credentials.credentials, db, expected_type=expected_type)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from None
    except (jwt.InvalidTokenError, WrongTokenType, AccessRevoked):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None


def get_current_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TenantContext:
    ctx = _header_context(credentials, db, expected_type="access")
    if ctx.is_demo and is_locked_in_demo(request.method, request.url.path):
        raise HTTPException(status.HTTP_403_FORBIDDEN, DEMO_LOCKED_MESSAGE)
    if ctx.must_change_password:
        # The API has no change-password endpoint, so a user holding an admin-issued
        # temporary password must finish the change on the web first — until then the
        # temporary password grants nothing beyond that one page.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Password change required: sign in to the web app to set a new password"
        )
    return ctx


def get_mfa_pending_context(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TenantContext:
    """For the two /auth/webauthn/login/* endpoints only: the client has passed the
    password check but not yet completed the second factor, so it holds a short-lived
    mfa_pending token (see api/v1/auth.py's login()) instead of a real access token.
    This must never be accepted anywhere a normal access token is (it grants no
    permissions — require_permission() is never used with it)."""
    return _header_context(credentials, db, expected_type="mfa_pending")


def require_permission(permission: str):
    def _check(ctx: TenantContext = Depends(get_current_context)) -> TenantContext:
        if permission not in ctx.permissions:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")
        return ctx

    return _check
