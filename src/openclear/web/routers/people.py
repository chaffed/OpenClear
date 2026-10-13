# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""People: a customer's own administrators managing its people (CUSTOMER_ADMIN_PLAN.md, CA2;
services/customer_people_service.py), and the invitation link anyone invited follows."""

import csv
import io
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.account import Account
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.services import audit_log_service, customer_people_service
from openclear.services.customer_people_service import PeopleError
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.rate_limit import rate_limit
from openclear.web.security import verify_csrf

router = APIRouter(tags=["web-people"])
_manage = require_web_permission("customer_user:manage")


def _record(db: Session, ctx: TenantContext, action: str, summary: str) -> None:
    """Every change a customer administrator makes is in the bank's action log, against the
    customer, which is also what the customer's own activity page reads."""
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action=action, summary=summary,
                                     resource_type="customer", resource_id=ctx.customer_id)


def _accounts(db: Session, ctx: TenantContext) -> list[Account]:
    return list(db.execute(select(Account).where(Account.tenant_id == ctx.tenant_id, Account.customer_id == ctx.customer_id)
                           .order_by(Account.account_number)).scalars())


def _back(message: str) -> RedirectResponse:
    return RedirectResponse("/ui/people?flash=" + quote(message), status_code=303)


def _ids(values: list) -> list[uuid.UUID]:
    try:
        return [uuid.UUID(str(v)) for v in values]
    except ValueError:
        raise PeopleError("Choose accounts from the list.") from None


@router.get("/ui/people")
def people(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    try:
        rows = customer_people_service.list_people(db, ctx.tenant_id, ctx.customer_id)
    except PeopleError:
        raise WebNotFound() from None
    return render_template(request, "people/list.html", ctx=ctx, people=rows,
                           invitations=customer_people_service.pending_invitations(db, ctx.tenant_id, ctx.customer_id),
                           groups={g.id: g.name for g in customer_people_service.offered_groups(db, ctx.tenant_id)})


def _invite_form(request: Request, db: Session, ctx: TenantContext, *, error: str | None = None, values: dict | None = None,
                 status_code: int = 200) -> HTMLResponse:
    return render_template(request, "people/invite.html", ctx=ctx, groups=customer_people_service.offered_groups(db, ctx.tenant_id),
                           accounts=_accounts(db, ctx), error=error, values=values or {}, status_code=status_code)


@router.get("/ui/people/invite")
def invite_form(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    return _invite_form(request, db, ctx)


@router.post("/ui/people/invite")
async def invite(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                 _csrf: None = Depends(verify_csrf)) -> Response:
    form = await request.form()
    values = {"email": str(form.get("email") or ""), "security_group_id": str(form.get("security_group_id") or ""),
              "scope": str(form.get("scope") or "all"), "account_ids": [str(v) for v in form.getlist("account_id")],
              "require_webauthn": form.get("require_webauthn") == "true"}
    try:
        invitation = customer_people_service.invite(
            db, ctx.tenant_id, ctx.customer_id, email=values["email"],
            security_group_id=_ids([values["security_group_id"]])[0] if values["security_group_id"] else uuid.UUID(int=0),
            all_accounts=values["scope"] != "some", account_ids=_ids(values["account_ids"]),
            require_webauthn=values["require_webauthn"], invited_by_user_id=ctx.user_id,
            accept_base_url=get_settings().public_base_url or str(request.base_url),
        )
    except PeopleError as exc:
        db.rollback()
        return _invite_form(request, db, ctx, error=str(exc), values=values, status_code=422)
    _record(db, ctx, "customer_people.invite", f"Invited {invitation.email}")
    db.commit()
    return _back(f"Invitation sent to {invitation.email}.")


@router.post("/ui/people/invite/bulk")
async def invite_many(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                      _csrf: None = Depends(verify_csrf)) -> Response:
    """A CSV with columns email, role and (optional) accounts: account numbers separated by
    semicolons, blank for all. Each row is its own invitation; a bad row doesn't stop the rest."""
    form = await request.form()
    upload = form.get("file")
    text = (await upload.read()).decode("utf-8-sig", errors="replace") if upload is not None and hasattr(upload, "read") else ""
    groups = {g.name.lower(): g for g in customer_people_service.offered_groups(db, ctx.tenant_id)}
    numbers = {a.account_number: a.id for a in _accounts(db, ctx)}
    base = get_settings().public_base_url or str(request.base_url)
    results = []
    for line, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        email = row.get("email", "")
        try:
            group = groups.get(row.get("role", "").lower())
            if group is None:
                raise PeopleError(f"Role “{row.get('role', '')}” isn't one your bank offers.")
            wanted = [n.strip() for n in row.get("accounts", "").split(";") if n.strip()]
            unknown = [n for n in wanted if n not in numbers]
            if unknown:
                raise PeopleError(f"Not your organization's account: {', '.join(unknown)}.")
            with db.begin_nested():
                customer_people_service.invite(
                    db, ctx.tenant_id, ctx.customer_id, email=email, security_group_id=group.id, all_accounts=not wanted,
                    account_ids=[numbers[n] for n in wanted], invited_by_user_id=ctx.user_id, accept_base_url=base)
            results.append((line, email, None))
        except PeopleError as exc:
            results.append((line, email, str(exc)))
    sent = [r for r in results if r[2] is None]
    if sent:
        _record(db, ctx, "customer_people.invite", f"Invited {len(sent)} people from a file")
    db.commit()
    return render_template(request, "people/bulk_result.html", ctx=ctx, results=results, sent=len(sent))


@router.post("/ui/people/invitations/{invitation_id}/revoke")
def revoke_invitation(invitation_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                      _csrf: None = Depends(verify_csrf)) -> Response:
    try:
        invitation = customer_people_service.revoke_invitation(db, ctx.tenant_id, ctx.customer_id, invitation_id)
    except PeopleError:
        raise WebNotFound() from None
    _record(db, ctx, "customer_people.revoke_invitation", f"Withdrew the invitation to {invitation.email}")
    db.commit()
    return _back("Invitation withdrawn.")


@router.get("/ui/people/export.csv")
def export(db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> Response:
    rows = customer_people_service.list_people(db, ctx.tenant_id, ctx.customer_id)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["email", "role", "accounts", "status", "passkey_required", "last_sign_in"])
    for p in rows:
        writer.writerow([p.user.email, p.group.name if p.group else "", p.accounts, "active" if p.membership.is_active else "deactivated",
                         "yes" if p.membership.require_webauthn else "no",
                         p.user.last_login_at.isoformat() if p.user.last_login_at else ""])
    _record(db, ctx, "customer_people.export", f"Exported the list of {len(rows)} people")
    db.commit()
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="people.csv"'})


@router.get("/ui/people/activity")
def activity(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    """The customer's own people and sign-on changes, by its administrators and by the bank."""
    entries = list(db.execute(select(AuditLogEntry).where(
        AuditLogEntry.tenant_id == ctx.tenant_id, AuditLogEntry.resource_type == "customer",
        AuditLogEntry.resource_id == ctx.customer_id,
        AuditLogEntry.action.like("customer%")).order_by(AuditLogEntry.sequence.desc()).limit(200)).scalars())
    from openclear.domain.user import User

    actors = {u.id: u.email for u in db.execute(select(User).where(User.id.in_({e.actor_user_id for e in entries if e.actor_user_id}))).scalars()}
    return render_template(request, "people/activity.html", ctx=ctx, entries=entries, actors=actors)


def _person_page(request: Request, db: Session, ctx: TenantContext, membership_id: uuid.UUID, *, error: str | None = None,
                 status_code: int = 200) -> HTMLResponse:
    try:
        person = customer_people_service.get_person(db, ctx.tenant_id, ctx.customer_id, membership_id)
    except (PeopleError, StopIteration):
        raise WebNotFound() from None
    from openclear.services import account_access_service

    access = account_access_service.get_access(db, ctx.tenant_id, membership_id)
    groups = customer_people_service.offered_groups(db, ctx.tenant_id)
    if person.group is not None and person.group not in groups:
        groups = [person.group, *groups]  # a role the bank gave them stays selectable as it is
    return render_template(request, "people/person.html", ctx=ctx, person=person, groups=groups, accounts=_accounts(db, ctx),
                           access=access, error=error, status_code=status_code)


@router.get("/ui/people/{membership_id}")
def person(request: Request, membership_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> HTMLResponse:
    return _person_page(request, db, ctx, membership_id)


@router.post("/ui/people/{membership_id}")
async def update_person(request: Request, membership_id: uuid.UUID, db: Session = Depends(get_db),
                        ctx: TenantContext = Depends(_manage), _csrf: None = Depends(verify_csrf)) -> Response:
    form = await request.form()
    try:
        membership = customer_people_service.update_person(
            db, ctx.tenant_id, ctx.customer_id, membership_id,
            security_group_id=_ids([form.get("security_group_id") or uuid.UUID(int=0)])[0],
            require_webauthn=form.get("require_webauthn") == "true", all_accounts=form.get("scope") != "some",
            account_ids=_ids(form.getlist("account_id")),
        )
    except PeopleError as exc:
        db.rollback()
        return _person_page(request, db, ctx, membership_id, error=str(exc), status_code=422)
    person = customer_people_service.get_person(db, ctx.tenant_id, ctx.customer_id, membership.id)
    _record(db, ctx, "customer_people.update",
            f"Changed {person.user.email}: {person.group.name if person.group else ''}, {person.accounts}"
            f"{', passkey required' if membership.require_webauthn else ''}")
    db.commit()
    return _back(f"Saved {person.user.email}.")


def _action(request: Request, db: Session, ctx: TenantContext, membership_id: uuid.UUID, fn, action: str, done: str):
    try:
        result = fn()
    except PeopleError as exc:
        db.rollback()
        return _person_page(request, db, ctx, membership_id, error=str(exc), status_code=422)
    person = customer_people_service.get_person(db, ctx.tenant_id, ctx.customer_id, membership_id)
    _record(db, ctx, action, done.format(email=person.user.email))
    db.commit()
    return result, person


@router.post("/ui/people/{membership_id}/deactivate")
def deactivate(request: Request, membership_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
               _csrf: None = Depends(verify_csrf)) -> Response:
    out = _action(request, db, ctx, membership_id,
                  lambda: customer_people_service.set_active(db, ctx.tenant_id, ctx.customer_id, membership_id, False),
                  "customer_people.deactivate", "Deactivated {email}")
    return out if isinstance(out, Response) else _back(f"Deactivated {out[1].user.email}.")


@router.post("/ui/people/{membership_id}/reactivate")
def reactivate(request: Request, membership_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
               _csrf: None = Depends(verify_csrf)) -> Response:
    out = _action(request, db, ctx, membership_id,
                  lambda: customer_people_service.set_active(db, ctx.tenant_id, ctx.customer_id, membership_id, True),
                  "customer_people.reactivate", "Reactivated {email}")
    return out if isinstance(out, Response) else _back(f"Reactivated {out[1].user.email}.")


@router.post("/ui/people/{membership_id}/unlock")
def unlock(request: Request, membership_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
           _csrf: None = Depends(verify_csrf)) -> Response:
    out = _action(request, db, ctx, membership_id,
                  lambda: customer_people_service.unlock(db, ctx.tenant_id, ctx.customer_id, membership_id),
                  "customer_people.unlock", "Unlocked {email}")
    return out if isinstance(out, Response) else _back(f"Unlocked {out[1].user.email}.")


@router.post("/ui/people/{membership_id}/sign-out")
def sign_out(request: Request, membership_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
             _csrf: None = Depends(verify_csrf)) -> Response:
    out = _action(request, db, ctx, membership_id,
                  lambda: customer_people_service.sign_out_everywhere(db, ctx.tenant_id, ctx.customer_id, membership_id),
                  "customer_people.sign_out_everywhere", "Signed {email} out everywhere")
    return out if isinstance(out, Response) else _back(f"Signed {out[1].user.email} out everywhere.")


@router.post("/ui/people/{membership_id}/reset-password")
def reset_password(request: Request, membership_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
                   _csrf: None = Depends(verify_csrf)) -> Response:
    out = _action(request, db, ctx, membership_id,
                  lambda: customer_people_service.reset_password(db, ctx.tenant_id, ctx.customer_id, membership_id, actor_user_id=ctx.user_id),
                  "customer_people.reset_password", "Reset the password of {email}")
    if isinstance(out, Response):
        return out
    (user, temporary), _person = out
    response = render_template(request, "users/reset_password_result.html", ctx=ctx, email=user.email, temporary_password=temporary,
                               back_url="/ui/people")
    response.headers["Cache-Control"] = "no-store"
    return response


# --- Accepting an invitation (no sign-in: the link is the credential) ---------------------------

_accept_limit = rate_limit("invitation", limit_setting="oauth_token_rate_limit_per_minute")


def _accept_page(request: Request, opened, *, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    response = render_template(request, "people/accept.html", opened=opened, error=error, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"  # the link is a secret
    return response


@router.get("/ui/invitations/{token}")
def accept_form(request: Request, token: str, db: Session = Depends(get_db), _limit: None = Depends(_accept_limit)) -> HTMLResponse:
    return _accept_page(request, customer_people_service.open_invitation(db, token))


@router.post("/ui/invitations/{token}")
def accept(request: Request, token: str, password: str = Form(""), password_confirm: str = Form(""),
           current_password: str = Form(""), db: Session = Depends(get_db), _limit: None = Depends(_accept_limit),
           _csrf: None = Depends(verify_csrf)) -> Response:
    opened = customer_people_service.open_invitation(db, token)
    if opened is None:
        return _accept_page(request, None, status_code=410)
    try:
        if opened.existing_user is not None:
            user, _membership = customer_people_service.accept_as_existing(db, opened, current_password)
        else:
            if opened.password_sign_in and password != password_confirm:
                raise PeopleError("The two passwords don't match.")
            user, _membership = customer_people_service.accept_as_new(db, opened, password)
    except ValueError as exc:  # PeopleError, or a password the policy refuses
        db.commit()  # keeps a wrong password's count toward the lockout
        return _accept_page(request, opened, error=str(exc), status_code=422)
    audit_log_service.record_action(db, opened.tenant.id, actor_user_id=user.id, channel="web", action="customer_people.accept",
                                    summary=f"{user.email} accepted the invitation to {opened.customer.name}",
                                    resource_type="customer", resource_id=opened.customer.id)
    db.commit()
    return RedirectResponse(f"/ui/login/{opened.tenant.slug}/{quote(opened.customer.customer_number)}?flash="
                            + quote("You're in. Sign in to continue."), status_code=303)
