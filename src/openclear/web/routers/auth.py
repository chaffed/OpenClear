# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import logging
import secrets
import uuid
from urllib.parse import quote

import jwt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.auth import saml_service
from openclear.auth.login_service import PasswordLoginOutcome, authenticate_password
from openclear.auth.oidc_service import OidcError, build_authorization_url, exchange_code_for_claims, new_code_verifier
from openclear.auth.security import (
    create_session_tokens,
    create_sso_state_token,
    create_token,
    decode_sso_state_token,
    decode_token,
)
from openclear.auth.webauthn_service import (
    WebauthnError,
    begin_authentication,
    begin_registration,
    complete_authentication,
    complete_registration,
)
from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext, bind_session_to_tenant
from openclear.domain.sso_connection import SsoProtocol
from openclear.domain.tenant import Tenant
from openclear.domain.user import User
from openclear.schemas.webauthn import AuthenticationVerifyRequest, RegistrationVerifyRequest
from openclear.services import (
    audit_log_service,
    customer_service,
    demo_tenant_service,
    device_service,
    session_service,
    sso_service,
    user_service,
)
from openclear.services.tenant_service import get_tenant_branding_by_slug
from openclear.web.deps import WebNotFound, get_mfa_pending_web_context, render_template
from openclear.web.security import (
    SSO_STATE_COOKIE_NAME,
    clear_auth_cookies,
    clear_mfa_cookie,
    clear_saml_state_cookie,
    clear_sso_state_cookie,
    read_cookie,
    safe_next_path,
    set_mfa_cookie,
    set_saml_state_cookie,
    set_session_cookies,
    set_sso_state_cookie,
    verify_csrf,
    verify_csrf_header,
    ACCESS_COOKIE_NAME,
)

router = APIRouter(prefix="/ui", tags=["web-auth"])
logger = logging.getLogger(__name__)

_NOT_AUTHORIZED_MESSAGE = "You're not authorized for OpenClear. Ask your administrator to add you to the required group."
_NOT_PROVISIONED_MESSAGE = "No OpenClear account exists for you yet. Ask your administrator to create one."
_SSO_REQUIRED_MESSAGE = "This organization requires single sign-on — use the button above."
_SSO_FAILED_MESSAGE = (
    "Single sign-on couldn't be completed. Please try again, and contact your administrator if it keeps happening."
)
_MFA_NOT_REPORTED_MESSAGE = ("Your organization's sign-on didn't confirm a second factor, which this organization requires. "
                             "Sign in again with your second factor, or ask your administrator.")
_IDENTITY_CONFLICT_MESSAGE = ("Your organization's sign-on sent a different identity for this email than before. "
                              "Ask your administrator to check your account before you sign in.")


def _invalid_message(db: Session, tenant_slug: str) -> str:
    """The same answer for a wrong password, an unknown email and a locked account, so the
    page doesn't reveal which emails have accounts (ASVS 5.0 6.3.8). It says when accounts
    lock; the account's owner is emailed when theirs does."""
    from openclear.auth.login_service import lockout_policy

    tenant = db.execute(select(Tenant).where(Tenant.slug == tenant_slug)).scalar_one_or_none()
    attempts, minutes, until_unlocked = lockout_policy(tenant)
    lasts = "until an administrator unlocks it" if until_unlocked else f"for {minutes} minutes"
    return (f"Invalid organization slug, email, or password. After {attempts} failed attempts an account is locked "
            f"{lasts}, and its owner is emailed.")


@router.get("/login")
def login_form(request: Request, next: str | None = None) -> HTMLResponse:
    # Unbranded — no tenant is known yet at this URL, so there's nothing to offer SSO
    # buttons for. A bookmarked/shared branded link (below) is what shows them.
    return render_template(request, "auth/login.html", next_path=safe_next_path(next))


@router.post("/login")
def login_submit(
    request: Request,
    tenant_slug: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/ui/"),
    db: Session = Depends(get_db),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    next_path = safe_next_path(next)
    # Reactive demo-tenant reset: runs *before* any identity for this request is
    # resolved, specifically so a reset-triggering login can't end up minting a token
    # against a user/membership row the reset just deleted out from under it -- a no-op
    # for every non-demo tenant. See demo_tenant_service.maybe_reset_if_demo_idle_by_slug.
    demo_tenant_service.maybe_reset_if_demo_idle_by_slug(db, tenant_slug)
    db.commit()
    result = authenticate_password(db, tenant_slug, email, password)
    # authenticate_password only flushes (see its docstring) — commit unconditionally,
    # even on a failed attempt, so the incremented failed_login_attempts/locked_until
    # actually persist rather than being discarded when this request's session closes.
    db.commit()
    if result.outcome != PasswordLoginOutcome.SUCCESS or result.identity is None:
        # Best-effort branding lookup by the submitted slug, purely cosmetic — a login
        # link that arrived branded (via /ui/login/{slug}) stays branded through a failed
        # attempt instead of dropping back to the generic form. Never used for the actual
        # auth decision above.
        if result.outcome == PasswordLoginOutcome.SSO_REQUIRED:
            message = _SSO_REQUIRED_MESSAGE
        elif result.outcome == PasswordLoginOutcome.PENDING_APPROVAL:
            message = PENDING_APPROVAL_MESSAGE
        else:  # a wrong password, an unknown email or a locked account all look alike
            message = _invalid_message(db, tenant_slug)
        return render_template(
            request,
            "auth/login.html",
            status_code=401,
            error=message,
            tenant_slug=tenant_slug,
            email=email,
            next_path=next_path,
            branding=get_tenant_branding_by_slug(db, tenant_slug),
        )

    identity = result.identity
    user, tenant, membership = identity.user, identity.tenant, identity.membership

    if identity.mfa_required:
        mfa_token = create_token(
            user_id=user.id,
            tenant_id=tenant.id,
            security_group_id=membership.security_group_id,
            customer_id=membership.customer_id,
            token_type="mfa_pending",
            token_version=user.token_version,
            signed_in_with="password",
        )
        next_step = "webauthn/setup" if identity.needs_webauthn_enrollment else "webauthn"
        response = RedirectResponse(f"/ui/login/{next_step}?next={quote(next_path)}", status_code=303)
        set_mfa_cookie(response, mfa_token)
        return response

    tokens = create_session_tokens(
        user_id=user.id,
        tenant_id=tenant.id,
        security_group_id=membership.security_group_id,
        customer_id=membership.customer_id,
        token_version=user.token_version,
        access_token_expire_minutes=tenant.access_token_expire_minutes,
        refresh_token_expire_minutes=tenant.refresh_token_expire_minutes,
        signed_in_with="password",
    )
    user_service.record_login(db, user.id, tenant_id=membership.tenant_id)
    db.commit()
    response = RedirectResponse(next_path, status_code=303)
    set_session_cookies(response, tokens)
    device_service.note_sign_in(db, request, response, user, bank_name=tenant.name)
    return response


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    # Deliberately does not require get_web_context — a user with an already-expired or
    # garbled token must still be able to clear cookies and get back to a working login
    # page, not get stuck in a redirect loop. When the cookie does hold a genuine token,
    # its session is revoked server-side (services/session_service.py), so a copy of
    # these cookies taken before logout stops working too — clearing cookies alone only
    # affects this browser.
    token = read_cookie(request, ACCESS_COOKIE_NAME)
    claims: dict = {}
    if token:
        try:
            claims = decode_token(token, verify_exp=False)
        except jwt.InvalidTokenError:
            claims = {}
        if claims.get("sid") and claims.get("sub"):
            session_service.revoke_session(db, session_id=uuid.UUID(claims["sid"]), user_id=uuid.UUID(claims["sub"]))
            from openclear.services import security_events

            security_events.emit(db, "auth.logout", tenant_id=uuid.UUID(claims["tenant_id"]) if claims.get("tenant_id") else None,
                                 actor_type="user", actor_id=claims["sub"])
            db.commit()
    embedded = bool(token and claims.get("emb"))
    response = RedirectResponse("/ui/embed/ended" if embedded else "/ui/login", status_code=303)
    clear_auth_cookies(response, embed=embedded)
    return response


def _session_tokens_from_mfa(ctx: TenantContext, user: User):
    """The tail shared by both WebAuthn completion routes: a new login session scoped
    exactly as the mfa_pending token was (ctx.security_group_id is already the
    membership's current group — auth/deps.py)."""
    return create_session_tokens(
        user_id=user.id,
        tenant_id=ctx.tenant_id,
        security_group_id=ctx.security_group_id,
        customer_id=ctx.customer_id,
        token_version=user.token_version,
        access_token_expire_minutes=ctx.access_token_expire_minutes,
        refresh_token_expire_minutes=ctx.refresh_token_expire_minutes,
        signed_in_with=ctx.signed_in_with,
    )


# --- WebAuthn second factor (login-time) ---
#
# The mfa_token cookie set above is HttpOnly and scoped to this path — the browser JS
# below (static/js/webauthn.js) never sees it or any other token; it only ever handles
# the WebAuthn options/credential JSON blobs. Both routes call auth/webauthn_service.py
# directly (in-process), not the JSON API — see docs/ARCHITECTURE.md, "Web UI vs. JSON API".


@router.get("/login/webauthn")
def login_webauthn_page(request: Request, next: str | None = None) -> HTMLResponse:
    return render_template(request, "auth/login_webauthn.html", next_path=safe_next_path(next))


@router.post("/login/webauthn/options")
def login_webauthn_options(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_mfa_pending_web_context),
    _csrf: None = Depends(verify_csrf_header),
) -> Response:
    user = db.get(User, ctx.user_id)
    try:
        options_json = begin_authentication(db, user, ctx.tenant_id)
    except WebauthnError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    db.commit()
    return Response(content=options_json, media_type="application/json")


@router.post("/login/webauthn/verify")
def login_webauthn_verify(
    request: Request,
    payload: AuthenticationVerifyRequest,
    next: str = "/ui/",
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_mfa_pending_web_context),
    _csrf: None = Depends(verify_csrf_header),
) -> JSONResponse:
    user = db.get(User, ctx.user_id)
    try:
        complete_authentication(db, user, ctx.tenant_id, payload.credential)
    except WebauthnError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    user_service.record_login(db, user.id, tenant_id=ctx.tenant_id)
    db.commit()

    next_path = safe_next_path(next)
    response = JSONResponse({"redirect": next_path})
    set_session_cookies(response, _session_tokens_from_mfa(ctx, user))
    clear_mfa_cookie(response)
    device_service.note_sign_in(db, request, response, user, bank_name=ctx.tenant_name)
    return response


# --- Mandatory WebAuthn enrollment (login-time) ---
#
# Reached only when a membership has require_webauthn set (auth/login_service.py) and
# this identity has no key registered in this tenant yet — the ordinary /login/webauthn
# challenge page above assumes a key already exists (begin_authentication raises
# otherwise). A successful registration ceremony already cryptographically proves
# possession of the new key, so it goes straight to minting tokens, same tail as
# login_webauthn_verify above, with no separate authentication challenge needed
# afterward. Same 3-path-segment shape as /login/{tenant_slug}/{customer_number} (see the
# NOTE below), so must stay registered before that catch-all, same as /login/webauthn.


@router.get("/login/webauthn/setup")
def login_webauthn_setup_page(request: Request, next: str | None = None) -> HTMLResponse:
    return render_template(request, "auth/login_webauthn_setup.html", next_path=safe_next_path(next))


@router.post("/login/webauthn/setup/options")
def login_webauthn_setup_options(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_mfa_pending_web_context),
    _csrf: None = Depends(verify_csrf_header),
) -> Response:
    user = db.get(User, ctx.user_id)
    options_json = begin_registration(db, user, ctx.tenant_id)
    db.commit()
    return Response(content=options_json, media_type="application/json")


@router.post("/login/webauthn/setup/verify")
def login_webauthn_setup_verify(
    request: Request,
    payload: RegistrationVerifyRequest,
    next: str = "/ui/",
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_mfa_pending_web_context),
    _csrf: None = Depends(verify_csrf_header),
) -> JSONResponse:
    user = db.get(User, ctx.user_id)
    try:
        complete_registration(db, user, ctx.tenant_id, payload.credential, nickname="Registered at mandatory sign-in")
    except WebauthnError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    user_service.record_login(db, user.id, tenant_id=ctx.tenant_id)
    db.commit()

    next_path = safe_next_path(next)
    response = JSONResponse({"redirect": next_path})
    set_session_cookies(response, _session_tokens_from_mfa(ctx, user))
    clear_mfa_cookie(response)
    device_service.note_sign_in(db, request, response, user, bank_name=ctx.tenant_name)
    return response


# --- Federated login (Okta / Azure AD via generic OIDC) ---
#
# Registered before /login/{tenant_slug} (which must stay last, see the NOTE further
# down) but that ordering concern doesn't actually apply here: every route in this
# section has a different number of path segments than /login/{tenant_slug} or
# /login/{tenant_slug}/{customer_number}, so none of them can ever be ambiguously
# matched against each other regardless of registration order.
#
# `tenant_id` is threaded through explicitly (a query param at /start, then carried
# inside the signed state cookie through to /callback) rather than re-derived from
# connection_id via an unscoped lookup at callback time. Two reasons: (1) the login page
# that renders the "Sign in with X" button already resolved its own tenant, so this is
# free; (2) SsoConnection is a normal RLS'd tenant-owned table (see its migration) — an
# unscoped `db.get(SsoConnection, connection_id)` before any tenant context has been
# established would silently match zero rows under real Postgres RLS enforcement (no
# `app.current_tenant_id` session setting yet to satisfy the policy), not just be a
# defense-in-depth gap. Carrying tenant_id explicitly means every SsoConnection query in
# this flow is a normal, already-scoped lookup like everywhere else in the app.


def _apply_rls_tenant(db: Session, tenant_id: uuid.UUID) -> None:
    # Mirrors auth/deps.py::decode_and_build_context's own inline RLS hint — this is the
    # one place outside of an authenticated request that needs it, since /start and
    # /callback run before any access token exists.
    bind_session_to_tenant(db, tenant_id)


def _redirect_uri(request: Request, connection_id: uuid.UUID) -> str:
    settings = get_settings()
    path = f"/ui/login/sso/{connection_id}/callback"
    if settings.public_base_url:
        return f"{settings.public_base_url.rstrip('/')}{path}"
    return str(request.url_for("sso_callback", connection_id=connection_id))


@router.get("/login/sso/{connection_id}/start", name="sso_start")
def sso_start(
    request: Request,
    connection_id: uuid.UUID,
    tenant_id: uuid.UUID,
    next: str | None = None,
    login_hint: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    _apply_rls_tenant(db, tenant_id)
    connection = sso_service.get_connection(db, tenant_id, connection_id)
    if connection is None or not connection.is_active:
        raise WebNotFound()

    next_path = safe_next_path(next)
    if connection.protocol == SsoProtocol.SAML:
        return _saml_start(request, connection, tenant_id, next_path)
    nonce = secrets.token_urlsafe(24)
    state = secrets.token_urlsafe(24)
    code_verifier = new_code_verifier()
    redirect_uri = _redirect_uri(request, connection.id)
    try:
        authorization_url = build_authorization_url(
            connection, redirect_uri=redirect_uri, state=state, nonce=nonce, code_verifier=code_verifier,
            login_hint=(login_hint or "")[:255] or None,
        )
    except OidcError as exc:
        logger.warning("SSO start failed for connection %s: %s", connection.id, exc)
        return render_template(request, "auth/login.html", status_code=502, error=_SSO_FAILED_MESSAGE)
    state_token = create_sso_state_token(
        connection_id=connection.id, tenant_id=tenant_id, nonce=nonce, next_path=next_path, state=state,
        code_verifier=code_verifier,
    )
    response = RedirectResponse(authorization_url, status_code=303)
    set_sso_state_cookie(response, state_token)
    return response


def _saml_start(request: Request, connection, tenant_id: uuid.UUID, next_path: str) -> Response:
    """SAML sign-in: a signed AuthnRequest to the identity provider. RelayState is a short
    random key (SAML suggests at most 80 bytes); the request ID and where to go afterwards
    travel in a signed cookie, which the RelayState must match when the answer comes back."""
    relay_state = secrets.token_urlsafe(18)
    try:
        url, request_id = saml_service.login_redirect(connection, saml_service.base_url(str(request.base_url)), relay_state)
    except saml_service.SamlError as exc:
        logger.warning("SAML start failed for connection %s: %s", connection.id, exc)
        return render_template(request, "auth/login.html", status_code=502, error=_SSO_FAILED_MESSAGE)
    state_token = create_sso_state_token(
        connection_id=connection.id, tenant_id=tenant_id, nonce=request_id, next_path=next_path, state=relay_state,
    )
    response = RedirectResponse(url, status_code=303)
    set_saml_state_cookie(response, state_token)
    return response


@router.get("/login/sso/{connection_id}/callback", name="sso_callback")
def sso_callback(
    request: Request,
    connection_id: uuid.UUID,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    state_cookie = read_cookie(request, SSO_STATE_COOKIE_NAME)

    def _error_page(message: str) -> HTMLResponse:
        response = render_template(request, "auth/login.html", status_code=401, error=message)
        clear_sso_state_cookie(response)
        return response

    if error or not code or not state_cookie:
        return _error_page("Single sign-on was cancelled or failed.")

    try:
        state_claims = decode_sso_state_token(state_cookie)
    except jwt.PyJWTError:
        return _error_page("Your sign-in attempt expired — please try again.")

    if state_claims["connection_id"] != str(connection_id) or not secrets.compare_digest(
        state_claims.get("state", ""), state
    ):
        return _error_page("Single sign-on state mismatch — please try again.")

    tenant_id = uuid.UUID(state_claims["tenant_id"])
    _apply_rls_tenant(db, tenant_id)
    connection = sso_service.get_connection(db, tenant_id, connection_id)
    if connection is None or not connection.is_active:
        raise WebNotFound()

    redirect_uri = _redirect_uri(request, connection.id)
    try:
        claims = exchange_code_for_claims(
            connection, code=code, redirect_uri=redirect_uri, expected_nonce=state_claims["nonce"],
            code_verifier=state_claims.get("code_verifier"),
        )
    except OidcError as exc:
        # Details go to the server log, not the login page: they can describe the
        # provider's (or the network's) internals.
        logger.warning("SSO callback failed for connection %s: %s", connection.id, exc)
        from openclear.services import security_events

        security_events.emit_detached("auth.sso.failed", tenant_id=tenant_id, outcome="failure", reason="oidc_rejected",
                                      target_type="sso_connection", target_id=connection.id, target_label=connection.display_name)
        return _error_page(_SSO_FAILED_MESSAGE)

    return finish_sso_login(request, db, tenant_id, connection, claims, state_claims.get("next_path"), _error_page)


PENDING_APPROVAL_MESSAGE = ("Your bank approves new people before they can use OpenClear, and hasn't yet. "
                            "You can sign in once it has; your organization's administrator will hear when it does.")


def finish_sso_login(request: Request, db: Session, tenant_id: uuid.UUID, connection, claims, next_path: str | None, error_page) -> Response:
    """The end of every federated sign-in (OIDC here, SAML in web/routers/saml.py): map the
    person's groups, record the login, and start the session."""
    result = sso_service.complete_sso_login(db, tenant_id, connection, claims)
    if result.outcome == "not_authorized":
        db.commit()
        return error_page(_NOT_AUTHORIZED_MESSAGE)
    if result.outcome == "mfa_not_reported":
        db.commit()  # keeps the security event
        return error_page(_MFA_NOT_REPORTED_MESSAGE)
    if result.outcome == "identity_conflict":
        db.commit()  # keeps the security event
        return error_page(_IDENTITY_CONFLICT_MESSAGE)
    if result.outcome == "not_provisioned":
        return error_page(_NOT_PROVISIONED_MESSAGE)
    if result.outcome == "pending_approval":
        db.commit()  # keeps a new person's waiting membership
        return error_page(PENDING_APPROVAL_MESSAGE)

    user, membership = result.user, result.membership
    user_service.record_login(db, user.id, tenant_id=membership.tenant_id)
    audit_log_service.record_action(
        db,
        tenant_id,
        actor_user_id=user.id,
        channel="web",
        action="user.login_sso",
        summary=f"Logged in via SSO ({connection.display_name})",
        resource_type="user",
        resource_id=user.id,
    )
    db.commit()

    login_tenant = db.get(Tenant, tenant_id)
    tokens = create_session_tokens(
        user_id=user.id,
        tenant_id=tenant_id,
        security_group_id=membership.security_group_id,
        customer_id=membership.customer_id,
        token_version=user.token_version,
        access_token_expire_minutes=login_tenant.access_token_expire_minutes,
        refresh_token_expire_minutes=login_tenant.refresh_token_expire_minutes,
        # A customer's sign-on vouches for this customer only (CUSTOMER_ADMIN_PLAN.md).
        confined=membership.customer_id is not None,
        signed_in_with="sso",
    )
    response = RedirectResponse(safe_next_path(next_path), status_code=303)
    set_session_cookies(response, tokens)
    clear_sso_state_cookie(response)
    clear_saml_state_cookie(response)
    device_service.note_sign_in(db, request, response, user, bank_name=login_tenant.name)
    return response


# NOTE: /login/{tenant_slug} and /login/{tenant_slug}/{customer_number} must be
# registered LAST (after every literal /login/* route above, in particular
# /login/webauthn*, /login/webauthn/setup*, and /login/sso/*) — FastAPI matches path
# patterns in registration order, and "webauthn"/"sso" would otherwise be captured as
# tenant_slug and rejected/mismatched (a nonexistent tenant, silently falling back to
# unbranded) before the real literal routes are ever tried. Same class of pitfall as
# /bulk vs /{item_id} elsewhere in this codebase. (In practice /login/sso/{connection_id}/
# start|callback have a different path-segment count than either of these two, so they
# can never actually collide — but /login/webauthn DOES share {tenant_slug}'s segment
# count, and /login/webauthn/setup shares {tenant_slug}/{customer_number}'s, which is the
# real reason this ordering matters.)


@router.get("/login/{tenant_slug}")
def branded_login_form(request: Request, tenant_slug: str, next: str | None = None, db: Session = Depends(get_db)) -> HTMLResponse:
    """A bookmarkable/shareable branded login link for one organization
    (services/tenant_service.py::get_tenant_branding_by_slug). An unknown or inactive
    slug falls back to the plain generic form rather than a 404 — same
    don't-reveal-which-part-was-wrong posture as authenticate_password."""
    branding = get_tenant_branding_by_slug(db, tenant_slug)
    sso_connections = sso_service.get_login_connections(db, branding.id) if branding else []
    return render_template(
        request,
        "auth/login.html",
        next_path=safe_next_path(next),
        tenant_slug=tenant_slug,
        branding=branding,
        sso_connections=sso_connections,
    )


@router.get("/login/{tenant_slug}/{customer_number}")
def customer_branded_login_form(
    request: Request, tenant_slug: str, customer_number: str, next: str | None = None, db: Session = Depends(get_db)
) -> HTMLResponse:
    """A customer's own bookmarkable login link — shows only THAT customer's SSO
    connections (and its own password-enforcement setting), not the bank's. The password
    form here still posts to the plain /ui/login handler unchanged:
    authenticate_password already resolves a customer-scoped membership correctly on its
    own, so this URL's only job is showing the right SSO buttons, not reimplementing
    password login."""
    branding = get_tenant_branding_by_slug(db, tenant_slug)
    if branding is None:
        return render_template(request, "auth/login.html", next_path=safe_next_path(next), tenant_slug=tenant_slug)

    customer = customer_service.get_customer_by_number(db, branding.id, customer_number)
    if customer is None or not customer.is_active:
        return render_template(
            request, "auth/login.html", next_path=safe_next_path(next), tenant_slug=tenant_slug, branding=branding
        )

    sso_connections = sso_service.get_login_connections(db, branding.id, customer_id=customer.id)
    return render_template(
        request,
        "auth/login.html",
        next_path=safe_next_path(next),
        tenant_slug=tenant_slug,
        branding=branding,
        sso_connections=sso_connections,
        customer_password_login_enabled=customer.password_login_enabled,
    )
