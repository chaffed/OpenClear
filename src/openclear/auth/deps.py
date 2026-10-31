# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import dataclasses
import uuid
from datetime import datetime, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from openclear.auth.permissions import CUSTOMER_SCOPE_MASKED_PERMISSIONS, CUSTOMER_SELF_SERVICE_PERMISSIONS
from openclear.auth.security import decode_token
from openclear.config import get_settings
from openclear.db.account_scope import bind_session_to_accounts, membership_accounts
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext, bind_session_to_tenant
from openclear.domain.customer import Customer
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.domain.security_group import SecurityGroup
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import session_service
from openclear.services.tenant_service import get_tenant_branding_by_id
from openclear.web.demo_guard import DEMO_LOCKED_MESSAGE, is_locked_in_demo

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


def _service_ended(customer) -> bool:
    """Its last service day is over (services/customer_offboarding_service.py): nobody there signs
    in, whatever a later sync set is_active to (REVIEW_1_33_PLAN.md O2, O5)."""
    from openclear.services import customer_offboarding_service

    return customer_offboarding_service.ended(customer)


def _pending_approval_count(db: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, user_id: uuid.UUID) -> int:
    stmt = select(func.count(ExceptionItem.id)).where(
        ExceptionItem.tenant_id == tenant_id,
        # A recommendation waiting, or a change to a decision waiting before its cut-off (UI_PLAN.md UI-D13).
        or_(ExceptionItem.status == ExceptionStatus.PENDING_APPROVAL, ExceptionItem.change_recommended_at.is_not(None)),
        or_(ExceptionItem.recommended_by_user_id.is_(None), ExceptionItem.recommended_by_user_id != user_id),
    )
    if customer_id is not None:
        stmt = stmt.where(ExceptionItem.customer_id == customer_id)
    return db.execute(stmt).scalar_one()


def _security_key_due(db: Session, tenant_id: uuid.UUID, membership: TenantMembership, payload: dict) -> datetime | None:
    """When a password user without a key must have registered one, while they're in the grace
    period (auth/security_key_policy.py), for the banner. SSO and embedded sessions are exempt."""
    if payload.get("am") != "password":
        return None
    from openclear.auth.security_key_policy import requirement
    from openclear.auth.webauthn_service import user_has_webauthn_credentials
    from openclear.domain.tenant import Tenant

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        return None
    required = requirement(db, tenant, membership)
    if not required.required or required.enforced or required.grace_ends is None:
        return None
    return None if user_has_webauthn_credentials(db, tenant_id, membership.user_id) else required.grace_ends


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

    if payload.get("sga"):
        return _support_context(db, payload, user, tenant_id)

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
    # Per-account access (db/account_scope.py): a customer's person limited to some of the
    # customer's accounts sees only those, on every query this request makes.
    account_ids = membership_accounts(db, membership)
    bind_session_to_accounts(db, account_ids, user_id=user_id)

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
    if not db.get(Tenant, tenant_id).is_active:  # loaded for the branding already; a bank frozen for deletion refuses every token
        raise AccessRevoked("Tenant is no longer active")

    permissions = frozenset(group.permissions)
    security_key_due = _security_key_due(db, tenant_id, membership, payload) if expected_type == "access" else None
    if expected_type == "access" and payload.get("sid"):
        session_service.note_session(
            db, session_id=uuid.UUID(payload["sid"]), tenant_id=tenant_id, user=user,
            signed_in_with="embed" if payload.get("emb") else payload.get("am"),
            expires_at=datetime.fromtimestamp(payload["sxp"], tz=timezone.utc) if payload.get("sxp") else None,
        )
    customer_name: str | None = None
    customer_banner_message: str | None = None
    if customer_id is not None:
        # Masked regardless of what the security group nominally contains — see
        # auth/permissions.py::CUSTOMER_SCOPE_MASKED_PERMISSIONS.
        permissions = permissions - CUSTOMER_SCOPE_MASKED_PERMISSIONS
        customer = db.get(Customer, customer_id)
        if customer is None or not customer.is_active or _service_ended(customer):
            raise AccessRevoked("Customer no longer exists")
        customer_name = customer.name
        customer_banner_message = customer.banner_message
        # Self-service only while the bank allows it (CUSTOMER_ADMIN_PLAN.md).
        permissions = permissions - {p for p, flag in CUSTOMER_SELF_SERVICE_PERMISSIONS.items() if not getattr(customer, flag)}
    else:
        permissions = permissions - set(CUSTOMER_SELF_SERVICE_PERMISSIONS)

    if user.is_service_account:
        # A read-only API client (docs/guides/bi.md) only ever reads, whatever its group allows.
        # The full data export is a read too.
        from openclear.domain.api_client import ApiClient

        read_only = db.execute(select(ApiClient.read_only).where(ApiClient.user_id == user_id)).scalar_one_or_none()
        if read_only:
            permissions = frozenset(p for p in permissions if p.endswith(":read") or p == "data_export:run")

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
        account_ids=account_ids,
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
        user_email=user.email,
        # other_organizations and pending_approval_count are for the navigation, so web pages fill
        # them in (web/deps.py::get_web_context); an API request doesn't pay for them
        # (FULL_REVIEW_PLAN.md FR9, O1).
        session_id=uuid.UUID(payload["sid"]) if payload.get("sid") else None,
        access_expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        session_expires_at=datetime.fromtimestamp(payload["sxp"], tz=timezone.utc) if payload.get("sxp") else None,
        embed_parent_origin=(payload.get("emb") or {}).get("po"),
        confined=bool(payload.get("cfd")),
        signed_in_with=payload.get("am"),
        security_key_due=security_key_due,
        authenticated_at=payload.get("auth"),
    )
    # Who this request is for, for logs, rate limits and security events (web/request_context.py).
    from openclear.web import request_context

    request_context.note_identity(tenant_id=tenant_id, tenant_slug=branding.slug, user_id=user_id, user_email=user.email)
    return ctx


def _support_context(db: Session, payload: dict, user: User, tenant_id: uuid.UUID) -> TenantContext:
    """A support session: the bank's grant, not a membership, says what this person may do,
    and only while it's active (services/support_access_service.py). Revoking it, or its
    period ending, ends the session on the next request."""
    from openclear.services import support_access_service

    grant = support_access_service.active_grant(db, uuid.UUID(payload["sga"]), user_id=user.id, tenant_id=tenant_id)
    if grant is None or user.platform_role not in support_access_service.PLATFORM_ROLES:
        raise AccessRevoked("Support access has ended")
    branding = get_tenant_branding_by_id(db, tenant_id)
    if branding is None:
        raise AccessRevoked("Tenant no longer exists")
    if not db.get(Tenant, tenant_id).is_active:  # loaded for the branding already; a bank frozen for deletion refuses every token
        raise AccessRevoked("Tenant is no longer active")
    expires_at = grant.expires_at if grant.expires_at.tzinfo else grant.expires_at.replace(tzinfo=timezone.utc)
    ctx = TenantContext(
        tenant_id=tenant_id, user_id=user.id, security_group_id=uuid.UUID(payload["security_group_id"]),
        permissions=support_access_service.permissions_for(grant), tenant_slug=branding.slug, tenant_name=branding.name,
        accent_color=branding.accent_color, has_logo=branding.has_logo, has_favicon=branding.has_favicon,
        customer_id=None, customer_name=None, support_email=branding.support_email, support_phone=branding.support_phone,
        website=branding.website, address_line1=branding.address_line1, address_line2=branding.address_line2, city=branding.city,
        state=branding.state, postal_code=branding.postal_code, banner_message=branding.banner_message, is_demo=branding.is_demo,
        access_expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc), session_expires_at=expires_at,
        support_grant_id=grant.id, support_expires_at=expires_at, support_write=grant.write_access,
    )
    from openclear.web import request_context

    request_context.note_identity(tenant_id=tenant_id, tenant_slug=branding.slug, user_id=user.id, user_email=user.email)
    return ctx


def _header_context(credentials: HTTPAuthorizationCredentials, db: Session, *, expected_type: str) -> TenantContext:
    try:
        return decode_and_build_context(credentials.credentials, db, expected_type=expected_type)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from None
    except (jwt.InvalidTokenError, WrongTokenType, AccessRevoked) as exc:
        _token_refused(type(exc).__name__)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None


def _token_refused(reason: str) -> None:
    """A bearer token that isn't valid (forged, revoked, signed out): a security event,
    sampled per source so a flood is one event a minute. Expired tokens are normal and
    aren't recorded."""
    from openclear.services import security_events
    from openclear.web import request_context

    context = request_context.current()
    security_events.emit_detached(
        "auth.token.failed", sampled_key=f"bearer|{context.client_ip if context else ''}", outcome="failure",
        reason={"InvalidSignatureError": "invalid_signature", "WrongTokenType": "wrong_token_type",
                "AccessRevoked": "revoked"}.get(reason, "invalid_token"),
    )


def _refused(name: str, ctx: TenantContext, reason: str, *, key: str) -> None:
    """An authenticated request refused (a missing permission, an act-as the client may not
    make): a security event, sampled per person and reason."""
    from openclear.services import security_events

    security_events.emit_detached(
        name, sampled_key=f"{ctx.user_id}|{key}", tenant_id=ctx.tenant_id, outcome="failure", reason=reason[:60],
        actor_type="user", actor_id=ctx.user_id,
    )


ACT_AS_HEADER = "openclear-act-as"
ACT_AS_CUSTOMER_HEADER = "openclear-act-as-customer"


ACT_AS_SUBJECT_HEADER = "openclear-act-as-subject"


def _acting_client(db: Session, ctx: TenantContext, why: str):
    from openclear.domain.api_client import ApiClient

    client = db.execute(
        select(ApiClient).where(ApiClient.user_id == ctx.user_id, ApiClient.tenant_id == ctx.tenant_id, ApiClient.revoked_at.is_(None))
    ).scalar_one_or_none()
    if client is None or not client.can_act_for_users:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Only an API client allowed to act for users can {why}")
    return client


def _membership_for(request: Request, db: Session, ctx: TenantContext, target: User | None) -> TenantMembership:
    if target is None or not target.is_active or target.is_service_account:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No such active user in this organization")
    memberships = [
        m for m in db.execute(select(TenantMembership).where(
            TenantMembership.user_id == target.id, TenantMembership.tenant_id == ctx.tenant_id, TenantMembership.is_active.is_(True),
        )).scalars()
        if ctx.customer_id is None or m.customer_id == ctx.customer_id
    ]
    customer_number = request.headers.get(ACT_AS_CUSTOMER_HEADER)
    if customer_number:
        wanted = db.execute(select(Customer.id).where(Customer.tenant_id == ctx.tenant_id, Customer.customer_number == customer_number)).scalar_one_or_none()
        memberships = [m for m in memberships if m.customer_id == wanted]
    if not memberships:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No such active user in this organization")
    if len(memberships) > 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That user has several memberships here; send OpenClear-Act-As-Customer too")
    return memberships[0]


def _acting_context(request: Request, db: Session, ctx: TenantContext, client, target: User, membership: TenantMembership) -> TenantContext:
    """The context of a client acting for a person: their identity and scope, with only the
    permissions both hold, and (for writes) an audit entry naming the client."""
    from openclear.services import audit_log_service

    if target.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That user must change their password before anything can be done for them")
    group = db.get(SecurityGroup, membership.security_group_id)
    permissions = frozenset(group.permissions if group else ()) & ctx.permissions
    customer = db.get(Customer, membership.customer_id) if membership.customer_id else None
    if customer is not None:
        permissions = permissions - CUSTOMER_SCOPE_MASKED_PERMISSIONS
    bind_session_to_tenant(db, ctx.tenant_id, user_id=target.id)
    acting = dataclasses.replace(
        ctx, user_id=target.id, security_group_id=membership.security_group_id, permissions=permissions,
        customer_id=membership.customer_id, customer_name=customer.name if customer else None,
        customer_banner_message=customer.banner_message if customer else None, must_change_password=False,
        session_id=None, acting_api_client_id=client.id,
    )
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        # The action's own audit entry names the person; this one shows a system did it for them.
        audit_log_service.record_action(
            db, ctx.tenant_id, actor_user_id=client.user_id, channel="api", action="api_client.act_as",
            summary=f"{client.name} acted for {target.email}: {request.method} {request.url.path}",
            resource_type="user", resource_id=target.id,
        )
    return acting


def _act_as(request: Request, db: Session, ctx: TenantContext, email: str) -> TenantContext:
    """An API client acting for one of its bank's users (SDK_PLAN.md P0): an online
    banking provider's server making a call for the person signed in to it, so the action
    is theirs (and the maker-checker rules see them). Only for clients the bank allowed;
    only for an existing, active member (of the client's customer, if it has one); and only
    with the permissions both the client and the user hold."""
    client = _acting_client(db, ctx, "send OpenClear-Act-As")
    target = db.execute(select(User).where(func.lower(User.email) == email.strip().lower())).scalar_one_or_none()
    return _acting_context(request, db, ctx, client, target, _membership_for(request, db, ctx, target))


def _act_as_subject(request: Request, db: Session, ctx: TenantContext, subject: str) -> TenantContext:
    """Like _act_as, naming the person by the online banking provider's own id for them
    (SDK_PLAN.md phase F): an id that doesn't change when an email does. The client must
    be linked to that provider's SSO connection, and the person must have signed in through
    it (or been exchanged) at least once."""
    from openclear.services import sso_service

    client = _acting_client(db, ctx, "send OpenClear-Act-As-Subject")
    connection = sso_service.get_connection(db, ctx.tenant_id, client.sso_connection_id) if client.sso_connection_id else None
    if connection is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This API client isn't linked to an SSO connection")
    target = sso_service.find_identity_user(db, connection, subject.strip())
    return _acting_context(request, db, ctx, client, target, _membership_for(request, db, ctx, target))


def _act_for_exchange(request: Request, db: Session, ctx: TenantContext, obo: dict) -> TenantContext:
    """A token from token exchange (api/v1/oauth.py) acts for the person it was issued for,
    in the membership the exchange resolved, re-checked on every request."""
    client = _acting_client(db, ctx, "use an exchanged token")
    try:
        target = db.get(User, uuid.UUID(obo["uid"]))
        membership = db.get(TenantMembership, uuid.UUID(obo["mid"]))
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None
    if (
        target is None or not target.is_active or membership is None or not membership.is_active
        or membership.user_id != target.id or membership.tenant_id != ctx.tenant_id
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The person this token acts for no longer has access")
    return _acting_context(request, db, ctx, client, target, membership)


def get_current_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> TenantContext:
    ctx = _header_context(credentials, db, expected_type="access")
    obo = decode_token(credentials.credentials).get("obo")
    act_as, act_as_subject = request.headers.get(ACT_AS_HEADER), request.headers.get(ACT_AS_SUBJECT_HEADER)
    base = ctx
    try:
        if obo:
            if act_as or act_as_subject:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "An exchanged token already acts for someone")
            ctx = _act_for_exchange(request, db, ctx, obo)
        elif act_as and act_as_subject:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send OpenClear-Act-As or OpenClear-Act-As-Subject, not both")
        elif act_as:
            ctx = _act_as(request, db, ctx, act_as)
        elif act_as_subject:
            ctx = _act_as_subject(request, db, ctx, act_as_subject)
    except HTTPException as exc:
        if exc.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
            _refused("auth.act_as.refused", base, str(exc.detail), key="act_as")
        raise
    if ctx.acting_api_client_id:
        from openclear.web import request_context

        request_context.note_identity(user_id=ctx.user_id, api_client_id=ctx.acting_api_client_id)
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
            _refused("access.denied", ctx, permission, key=permission)
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")
        return ctx

    return _check


RECENT_SIGN_IN_REQUIRED = "For your security, sign in again to change your security keys."


def recently_signed_in(ctx: TenantContext) -> bool:
    """Whether the person proved who they are within Settings.recent_sign_in_minutes: needed to
    add or remove a security key, so a stolen session can't swap in its own (ASVS 5.0 7.5.1).
    A session from before 1.22 has no sign-in time and isn't recent."""
    if ctx.authenticated_at is None:
        return False
    age = datetime.now(timezone.utc).timestamp() - ctx.authenticated_at
    return age <= get_settings().recent_sign_in_minutes * 60
