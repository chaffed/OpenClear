# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.auth.security import create_session_tokens, create_token
from openclear.auth.webauthn_service import user_has_webauthn_credentials
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext, acting_as_tenant
from openclear.domain.user import User
from openclear.services import session_service, user_service
from openclear.web.deps import get_web_context, render_template
from openclear.web.security import set_mfa_cookie, set_session_cookies, verify_csrf

router = APIRouter(prefix="/ui/switch-tenant", tags=["web-tenant-switch"])


@router.get("")
def switch_tenant_form(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context)
) -> HTMLResponse:
    """Also doubles as "switch customer scope" — a user can now hold several
    memberships within the same tenant (one per customer, or tenant-wide plus
    per-customer overrides), so the current one is excluded by its exact
    (tenant_id, customer_id) pair, not just by tenant_id."""
    memberships = [
        m
        for m in user_service.list_memberships_for_user(db, ctx.user_id)
        if not (m.membership.tenant_id == ctx.tenant_id and m.membership.customer_id == ctx.customer_id)
    ]
    return render_template(request, "tenant_switch.html", ctx=ctx, memberships=memberships)


@router.post("")
def switch_tenant(
    membership_id: uuid.UUID = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """No password re-entry — the caller already holds a valid session for this
    identity; this only re-validates that the identity actually holds the target
    membership, then mints tokens scoped to it (tenant, and — new — customer). Keyed on
    the membership's own id rather than tenant_id, since more than one membership can now
    share a tenant_id (see domain/tenant_membership.py).

    A target membership with require_webauthn set is the one exception to "no re-entry":
    it re-mints an mfa_pending token instead and routes through the same
    /ui/login/webauthn* challenge (or /setup, if no key exists yet in the TARGET tenant —
    credentials are tenant-scoped) used at login time, so a hijacked session cookie can't
    silently pivot into one of these scopes without proving possession of the key again.
    Switching between two ordinary memberships is unaffected."""
    target = next(
        (m for m in user_service.list_memberships_for_user(db, ctx.user_id) if m.membership.id == membership_id), None
    )
    if target is None:
        return RedirectResponse("/ui/switch-tenant?error=Not+a+member+of+that+organization.", status_code=303)

    user = db.get(User, ctx.user_id)

    if target.membership.require_webauthn:
        mfa_token = create_token(
            user_id=ctx.user_id,
            tenant_id=target.tenant.id,
            security_group_id=target.membership.security_group_id,
            customer_id=target.membership.customer_id,
            token_type="mfa_pending",
            token_version=user.token_version,
        )
        with acting_as_tenant(db, target.tenant.id):  # Postgres RLS: keys belong to the target bank
            has_key = user_has_webauthn_credentials(db, target.tenant.id, ctx.user_id)
        next_step = "webauthn" if has_key else "webauthn/setup"
        response = RedirectResponse(f"/ui/login/{next_step}?next={quote('/ui/')}", status_code=303)
        set_mfa_cookie(response, mfa_token)
        return response

    # The switch replaces this browser's cookies, so the session they belonged to is
    # ended rather than left valid for anyone holding a copy of them. (Not done on the
    # WebAuthn branch above: abandoning that challenge shouldn't sign the user out.)
    if ctx.session_id is not None:
        session_service.revoke_session(db, session_id=ctx.session_id, user_id=ctx.user_id)
        db.commit()
    tokens = create_session_tokens(
        user_id=ctx.user_id,
        tenant_id=target.tenant.id,
        security_group_id=target.membership.security_group_id,
        customer_id=target.membership.customer_id,
        token_version=user.token_version,
        access_token_expire_minutes=target.tenant.access_token_expire_minutes,
        refresh_token_expire_minutes=target.tenant.refresh_token_expire_minutes,
    )
    response = RedirectResponse("/ui/", status_code=303)
    set_session_cookies(response, tokens)
    return response
