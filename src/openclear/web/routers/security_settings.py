# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.auth.webauthn_service import (
    WebauthnError,
    begin_registration,
    complete_registration,
    delete_credential,
    list_credentials,
    record_key_change,
)
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.notification import NotificationType
from openclear.domain.user import User
from openclear.schemas.webauthn import RegistrationVerifyRequest
from openclear.auth.deps import RECENT_SIGN_IN_REQUIRED, recently_signed_in
from openclear.auth.password_policy import describe
from openclear.auth.security import create_session_tokens
from openclear.services import audit_log_service, notification_service, session_service, user_service
from openclear.web.deps import PASSWORD_CHANGE_PATH, get_web_context, render_template
from openclear.web.security import set_session_cookies, verify_csrf, verify_csrf_header
from openclear.web.redirects import see_other

router = APIRouter(prefix="/ui/security", tags=["web-security-settings"])

# ACCOUNT_LOCKED's email is not preference-controlled (services/notification_service.py's
# _ALWAYS_EMAIL_TYPES) -- excluded here entirely rather than rendered as a disabled,
# always-checked control, since there's nothing for the user to meaningfully see or do
# with it.
_PREFERENCE_TYPES = [
    t for t in NotificationType if t not in (NotificationType.ACCOUNT_LOCKED, NotificationType.PASSWORD_CHANGED, NotificationType.INVITATION,
                                             NotificationType.PASSWORD_RESET_LINK, NotificationType.SECURITY_KEY_CHANGED,
                                             NotificationType.NEW_DEVICE_SIGN_IN, NotificationType.EMAIL_CHANGE_CONFIRM,
                                             NotificationType.EMAIL_CHANGED, NotificationType.MALWARE_DETECTED,
                                             NotificationType.INACTIVITY_WARNING)
]

_NOTIFICATION_TYPE_LABELS = {
    NotificationType.EXCEPTION_CREATED: "New exception ready for review",
    NotificationType.RECOMMENDATION_AWAITING_APPROVAL: "A recommendation is awaiting your approval",
    NotificationType.ACCOUNT_UNLOCKED: "Your account was unlocked",
    NotificationType.EXCEPTION_AUTO_DECIDED: "An exception was auto-decided",
    NotificationType.DEADLINE_REMINDER: "Reminder: items to decide before the cutoff",
    NotificationType.CUSTOMER_USER_DECIDED: "Your bank approved or rejected someone you added",
    NotificationType.ATTESTATION_REQUESTED: "Your bank asks you to attest your responsibilities for the year",
    NotificationType.CHANGE_REQUESTED: "A settings change is waiting for your approval",
    NotificationType.CHANGE_DECIDED: "A settings change you asked for was decided",
    NotificationType.APPROVER_GONE: "A second-person rule has nobody else to approve",
}


def _types_for(ctx: TenantContext) -> list[NotificationType]:
    """The types this person can receive: the bank's decisions on new people only reach a
    customer's administrators (MOBILE_PLAN.md R6), and its yearly request to attest only the
    customer's people who can attest (CONTROLS_PLAN.md CT-D13)."""
    managing = ctx.customer_id is not None and "customer_user:manage" in ctx.permissions
    attesting = ctx.customer_id is not None and "compliance:attest" in ctx.permissions
    hidden = {NotificationType.CUSTOMER_USER_DECIDED: not managing, NotificationType.ATTESTATION_REQUESTED: not attesting}
    return [t for t in _PREFERENCE_TYPES if not hidden.get(t, False)]


@router.get("")
def security_settings(
    request: Request,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
) -> HTMLResponse:
    credentials = list_credentials(db, ctx.tenant_id, ctx.user_id)
    sessions = session_service.active_sessions(db, ctx.tenant_id, [ctx.user_id])
    from openclear.services import email_change_service

    return render_template(request, "security/webauthn.html", ctx=ctx, credentials=credentials, sessions=sessions,
                           describe_browser=session_service.describe_browser, email=db.get(User, ctx.user_id).email,
                           pending_email=email_change_service.pending(db, ctx.user_id))


@router.post("/email")
def change_my_email(
    request: Request,
    new_email: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """A link to the new address confirms it (OWASP_PLAN.md OW-D31). After a recent sign-in, and
    not for someone signed in through their organization's sign-on, which owns their address."""
    from openclear.config import get_settings
    from openclear.services import email_change_service

    if ctx.signed_in_with == "sso":
        return see_other(f"/ui/security?error={quote('Your organization’s sign-on sets your address; ask its administrator.')}")
    if not recently_signed_in(ctx):
        return see_other(f"/ui/security?error={quote(RECENT_SIGN_IN_REQUIRED)}")
    try:
        change = email_change_service.request_change(db, db.get(User, ctx.user_id), new_email, tenant_id=ctx.tenant_id,
                                                     requested_by_user_id=ctx.user_id,
                                                     base_url=get_settings().public_base_url or str(request.base_url))
    except email_change_service.EmailChangeError as exc:
        db.rollback()
        return see_other(f"/ui/security?error={quote(str(exc))}")
    db.commit()
    return see_other(f"/ui/security?flash={quote(f'We sent a link to {change.new_email}. Open it to finish the change.')}")


@router.post("/sessions/{session_id}/end")
def end_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Signs out one of the person's own other sessions (ASVS 5.0 7.5.2)."""
    mine = [s for s in session_service.active_sessions(db, ctx.tenant_id, [ctx.user_id]) if s.id == session_id]
    if not mine or session_id == ctx.session_id:
        return see_other(f"/ui/security?error={quote('That session has already ended, or is this one.')}")
    session_service.end_sessions(db, mine)
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="user.session.ended",
                                    summary="Signed out one of their other sessions", resource_type="user", resource_id=ctx.user_id)
    db.commit()
    return see_other(f"/ui/security?flash={quote('Signed out of that session.')}")


def _render_password_form(
    request: Request, db: Session, ctx: TenantContext, *, error: str | None = None, required: bool = False
) -> HTMLResponse:
    policy = user_service.password_policy_for_user(db, ctx.user_id)
    return render_template(
        request,
        "security/password.html",
        ctx=ctx,
        error=error,
        required=required or ctx.must_change_password,
        policy_hint=describe(policy) if policy else None,
        status_code=400 if error else 200,
    )


@router.get("/password")
def change_password_form(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context)
) -> HTMLResponse:
    return _render_password_form(request, db, ctx)


@router.post("/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    if new_password != confirm_password:
        return _render_password_form(request, db, ctx, error="The new passwords don't match.")
    try:
        user_service.change_own_password(
            db, ctx.tenant_id, ctx.user_id, current_password=current_password, new_password=new_password
        )
    except user_service.PasswordChangeError as exc:
        db.commit()  # persist a wrong-current-password strike toward the lockout
        return _render_password_form(request, db, ctx, error=str(exc))
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="user.password_change",
        summary="Changed own password" + (" (required after an administrator reset)" if ctx.must_change_password else ""),
        resource_type="user",
        resource_id=ctx.user_id,
    )
    db.commit()
    destination = "/ui/" if ctx.must_change_password else PASSWORD_CHANGE_PATH
    response = see_other(f"{destination}?flash=" + quote("Your password has been changed."))
    # change_own_password signed out every session (token_version bumped), this one
    # included — re-issue tokens for it so the user stays on the page they're on.
    _reissue_current_session(response, ctx, db.get(User, ctx.user_id))
    return response


@router.post("/sign-out-others")
def sign_out_other_sessions(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Ends every other session this person has (other devices, other browsers), keeping
    this one — the same "sign out everywhere, then re-issue this session" sequence as a
    password change."""
    user = db.get(User, ctx.user_id)
    session_service.revoke_all_sessions(db, user)
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="user.sign_out_other_sessions",
        summary="Signed out of all other sessions",
        resource_type="user",
        resource_id=ctx.user_id,
    )
    db.commit()
    response = see_other("/ui/security?flash=" + quote("You've been signed out on all other devices."))
    _reissue_current_session(response, ctx, user)
    return response


def _reissue_current_session(response: Response, ctx: TenantContext, user: User) -> None:
    """After a sign-out-everywhere (token_version bump) the current session's tokens are
    dead too; this re-issues them — same session id and maximum length — so the person
    who asked stays signed in."""
    set_session_cookies(
        response,
        create_session_tokens(
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id,
            security_group_id=ctx.security_group_id,
            customer_id=ctx.customer_id,
            token_version=user.token_version,
            access_token_expire_minutes=ctx.access_token_expire_minutes,
            refresh_token_expire_minutes=ctx.refresh_token_expire_minutes,
            session_id=ctx.session_id,
            session_expires_at=ctx.session_expires_at,
            # The session's own claims: dropping them would let a confined or embedded
            # session switch organization after "Sign out other devices".
            embed={"po": ctx.embed_parent_origin} if ctx.embed_parent_origin else None,
            confined=ctx.confined,
            signed_in_with=ctx.signed_in_with,
            authenticated_at=ctx.authenticated_at,
        ),
    )


@router.get("/notifications")
def notification_settings(
    request: Request,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
) -> HTMLResponse:
    user = db.get(User, ctx.user_id)
    preferences = notification_service.get_preferences(db, ctx.user_id)
    rows = [
        {
            "type": t,
            "label": _NOTIFICATION_TYPE_LABELS[t],
            "email_enabled": preferences[t].email_enabled if t in preferences else True,
            "sms_enabled": preferences[t].sms_enabled if t in preferences else False,
        }
        for t in _types_for(ctx)
    ]
    return render_template(request, "security/notifications.html", ctx=ctx, phone=user.phone, rows=rows)


@router.post("/notifications/phone")
def update_phone(
    request: Request,
    phone: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    user = db.get(User, ctx.user_id)
    notification_service.set_phone(db, user, phone.strip() or None)
    db.commit()
    return see_other("/ui/security/notifications")


@router.post("/notifications")
async def update_notification_preferences(
    request: Request,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    form = await request.form()
    for notification_type in _types_for(ctx):
        notification_service.set_preference(
            db, ctx.user_id, notification_type,
            email_enabled=f"email_{notification_type.value}" in form,
            sms_enabled=f"sms_{notification_type.value}" in form,
        )
    db.commit()
    return see_other("/ui/security/notifications")


@router.post("/webauthn/register/options")
def register_options(
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf_header),
) -> Response:
    if not recently_signed_in(ctx):
        return JSONResponse({"error": RECENT_SIGN_IN_REQUIRED}, status_code=403)
    user = db.get(User, ctx.user_id)
    options_json = begin_registration(db, user, ctx.tenant_id)
    db.commit()
    return Response(content=options_json, media_type="application/json")


@router.post("/webauthn/register/verify")
def register_verify(
    payload: RegistrationVerifyRequest,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf_header),
) -> JSONResponse:
    if not recently_signed_in(ctx):
        return JSONResponse({"error": RECENT_SIGN_IN_REQUIRED}, status_code=403)
    user = db.get(User, ctx.user_id)
    try:
        credential = complete_registration(db, user, ctx.tenant_id, payload.credential, nickname=payload.nickname)
    except WebauthnError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    record_key_change(db, ctx, user, added=True, nickname=credential.nickname, channel="web")
    db.commit()
    return JSONResponse({"id": str(credential.id), "nickname": credential.nickname})


@router.post("/webauthn/{credential_id}/delete")
def delete_credential_route(
    credential_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_web_context),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    if not recently_signed_in(ctx):
        return see_other(f"/ui/security?error={quote(RECENT_SIGN_IN_REQUIRED)}")
    nickname = next((c.nickname for c in list_credentials(db, ctx.tenant_id, ctx.user_id) if c.id == credential_id), None)
    deleted = delete_credential(db, ctx.tenant_id, ctx.user_id, credential_id)
    if deleted:
        record_key_change(db, ctx, db.get(User, ctx.user_id), added=False, nickname=nickname, channel="web")
    db.commit()
    # ASVS 7.4.3: after removing a factor, offer to end every other session.
    flash = ("Security key removed. If it was lost or stolen, use Sign out other devices below." if deleted
             else "Security key not found.")
    return see_other(f"/ui/security?flash={quote(flash)}")
