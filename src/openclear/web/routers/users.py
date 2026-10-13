# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import csv
import io
import json
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from openclear.auth.password_policy import describe as describe_password_policy
from openclear.auth.password_policy import effective_policy
from openclear.bulk_import.tabular import TabularParseError, parse_tabular_file
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.repositories.user_repo import UserRepository
from openclear.services import (
    account_access_service,
    audit_log_service,
    bulk_upload_file_service,
    bulk_upload_reversal_service,
    customer_service,
    security_group_service,
    user_service,
)
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/users", tags=["web-users"])

# user:manage is one of the permissions masked out of any customer-scoped membership's
# resolved permissions (auth/permissions.py::CUSTOMER_SCOPE_MASKED_PERMISSIONS), so every
# route below is unreachable with ctx.customer_id set — the "Customer" picker here is
# always about which customer a TENANT-WIDE admin is assigning the user/membership to,
# never about the admin's own scope.




def _people_only(
    membership_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("user:manage"))
) -> None:
    """An API client's service identity is managed on the API clients page, never here."""
    membership = TenantMembershipRepository(db, ctx.tenant_id).get(membership_id)
    user = UserRepository(db).get(membership.user_id) if membership is not None else None
    if user is not None and user.is_service_account:
        raise WebNotFound()

@router.get("")
def list_users(
    request: Request, page: int = 1, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
) -> HTMLResponse:
    page_obj = paginate(
        page=page,
        count_fn=lambda: user_service.count_tenant_users(db, ctx.tenant_id),
        list_fn=lambda **kw: user_service.list_tenant_users(db, ctx.tenant_id, order_by=TenantMembership.created_at.desc(), **kw),
    )
    return render_template(request, "users/list.html", ctx=ctx, page_obj=page_obj)


_EXPORT_COLUMNS = ("email", "security_group", "customer", "status", "membership_created_at", "last_login_at")


def _export_row(row: user_service.TenantUserRow) -> dict:
    return {
        "email": row.user.email,
        "security_group": row.security_group_name,
        "customer": row.customer_name or "bank-wide",
        "status": "active" if row.membership.is_active else "deactivated",
        "membership_created_at": row.membership.created_at.isoformat(),
        "last_login_at": row.user.last_login_at.isoformat() if row.user.last_login_at else None,
    }


_FORMULA_LEADING_CHARS = ("=", "+", "-", "@")


def _csv_safe(value: object) -> object:
    """Neutralizes CSV/spreadsheet formula injection: a cell whose value starts with
    =/+/-/@ can execute as a formula the moment this file is opened in Excel/Sheets.
    Prefixing a literal apostrophe is the standard mitigation both accept as "force this
    cell to plain text." Only applied to the CSV export — export_users_json's data isn't
    opened in a spreadsheet, so it's untouched (this must never alter what that route
    actually returns)."""
    if isinstance(value, str) and value.startswith(_FORMULA_LEADING_CHARS):
        return f"'{value}"
    return value


def _exported(db: Session, ctx: TenantContext, fmt: str, count: int) -> None:
    """Who has access is itself sensitive: an export is a security event (SIEM_PLAN.md)."""
    from openclear.services import security_events

    security_events.emit(db, "users.exported", tenant_id=ctx.tenant_id, details={"format": fmt, "users": count})
    db.commit()


@router.get("/export.csv")
def export_users_csv(
    db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("user:manage"))
) -> Response:
    rows = user_service.list_tenant_users(db, ctx.tenant_id)
    _exported(db, ctx, "csv", len(rows))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_EXPORT_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_safe(value) for key, value in _export_row(row).items()})
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=\"users.csv\""},
    )


@router.get("/export.json")
def export_users_json(
    db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("user:manage"))
) -> Response:
    rows = user_service.list_tenant_users(db, ctx.tenant_id)
    _exported(db, ctx, "json", len(rows))
    payload = json.dumps([_export_row(row) for row in rows], indent=2)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=\"users.json\""},
    )


@router.get("/new")
def new_user_form(
    request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("user:manage"))
) -> HTMLResponse:
    groups = security_group_service.list_security_groups(db, ctx.tenant_id)
    customers = customer_service.list_customers(db, ctx.tenant_id)
    tenant = db.get(Tenant, ctx.tenant_id)
    password_policy_hint = describe_password_policy(effective_policy(tenant, None))
    return render_template(
        request, "users/new.html", ctx=ctx, groups=groups, customers=customers, password_policy_hint=password_policy_hint
    )


@router.post("")
def create_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(""),
    security_group_id: uuid.UUID = Form(...),
    customer_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    resolved_customer_id = uuid.UUID(customer_id) if customer_id else None
    result = user_service.add_user(
        db, ctx.tenant_id, email=email, password=password, security_group_id=security_group_id, customer_id=resolved_customer_id
    )

    if result.outcome == "created":
        created_user = UserRepository(db).get_by_email(email)
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="user.create",
            summary=f"Added user {email}",
            resource_type="user",
            resource_id=created_user.id if created_user else None,
        )
        db.commit()
        return RedirectResponse("/ui/users?flash=User+created.", status_code=303)
    if result.outcome == "already_member":
        db.rollback()
        return RedirectResponse("/ui/users?flash=That+person+already+has+access+to+this+organization.", status_code=303)
    if result.outcome == "needs_confirmation":
        db.rollback()
        group = security_group_service.get_security_group(db, ctx.tenant_id, security_group_id)
        return render_template(
            request,
            "users/confirm_existing.html",
            ctx=ctx,
            email=email,
            security_group_id=security_group_id,
            security_group_name=group.name if group else "",
            customer_id=resolved_customer_id,
        )

    db.rollback()
    groups = security_group_service.list_security_groups(db, ctx.tenant_id)
    customers = customer_service.list_customers(db, ctx.tenant_id)
    tenant = db.get(Tenant, ctx.tenant_id)
    return render_template(
        request, "users/new.html", ctx=ctx, groups=groups, customers=customers, error=result.error, status_code=422,
        password_policy_hint=describe_password_policy(effective_policy(tenant, None)),
    )


@router.post("/confirm")
def confirm_existing_user(
    email: str = Form(...),
    security_group_id: uuid.UUID = Form(...),
    customer_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    resolved_customer_id = uuid.UUID(customer_id) if customer_id else None
    membership = user_service.confirm_cross_tenant_membership(
        db, ctx.tenant_id, email=email, security_group_id=security_group_id, customer_id=resolved_customer_id
    )
    if membership is None:
        db.rollback()
        return RedirectResponse("/ui/users?error=That+user+no+longer+exists.", status_code=303)
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="user.grant_cross_tenant_access",
        summary=f"Granted {email} access to this organization",
        resource_type="user",
        resource_id=membership.user_id,
    )
    db.commit()
    return RedirectResponse("/ui/users?flash=Access+granted.", status_code=303)


@router.post("/{membership_id}/deactivate", dependencies=[Depends(_people_only)])
def deactivate_user(
    membership_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    membership = user_service.deactivate_membership(db, ctx.tenant_id, membership_id)
    if membership is not None:
        actor = UserRepository(db).get(membership.user_id)
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="user.deactivate",
            summary=f"Deactivated access for {actor.email if actor else membership.user_id}",
            resource_type="user",
            resource_id=membership.user_id,
        )
    db.commit()
    if membership is None:
        return RedirectResponse("/ui/users?error=User+not+found.", status_code=303)
    return RedirectResponse("/ui/users?flash=Access+removed.", status_code=303)


@router.post("/{membership_id}/reactivate", dependencies=[Depends(_people_only)])
def reactivate_user(
    membership_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    membership = user_service.reactivate_membership(db, ctx.tenant_id, membership_id)
    if membership is not None:
        actor = UserRepository(db).get(membership.user_id)
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="user.reactivate",
            summary=f"Restored access for {actor.email if actor else membership.user_id}",
            resource_type="user",
            resource_id=membership.user_id,
        )
    db.commit()
    if membership is None:
        return RedirectResponse("/ui/users?error=User+not+found.", status_code=303)
    return RedirectResponse("/ui/users?flash=Access+restored.", status_code=303)


@router.post("/{membership_id}/unlock", dependencies=[Depends(_people_only)])
def unlock_user(
    membership_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Manual override for auth/login_service.py's auto-expiring lockout — see
    services/user_service.py::unlock_user."""
    unlocked = user_service.unlock_user(db, ctx.tenant_id, membership_id)
    if unlocked is not None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="user.unlock",
            summary=f"Unlocked account for {unlocked.email}",
            resource_type="user",
            resource_id=unlocked.id,
        )
    db.commit()
    if unlocked is None:
        return RedirectResponse("/ui/users?error=User+not+found.", status_code=303)
    return RedirectResponse("/ui/users?flash=Account+unlocked.", status_code=303)


@router.post("/{membership_id}/sign-out", dependencies=[Depends(_people_only)])
def sign_out_everywhere(
    membership_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """For a lost/stolen device or a suspected compromised session — see
    services/user_service.py::admin_sign_out_everywhere."""
    user = user_service.admin_sign_out_everywhere(db, ctx.tenant_id, membership_id, actor_user_id=ctx.user_id)
    if user is None:
        return RedirectResponse("/ui/users?error=User+not+found.", status_code=303)
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="user.sign_out_everywhere",
        summary=f"Signed {user.email} out of all sessions",
        resource_type="user",
        resource_id=user.id,
    )
    db.commit()
    return RedirectResponse("/ui/users?flash=" + quote(f"{user.email} has been signed out everywhere."), status_code=303)


@router.post("/{membership_id}/reset-password", dependencies=[Depends(_people_only)])
def reset_password(
    request: Request,
    membership_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    """Issues a one-time temporary password — see services/user_service.py::
    admin_reset_password for who can and can't be reset from here. Rendered directly as
    this POST's response (never a redirect) so the temporary password never appears in a
    URL, browser history, or a re-fetchable page; no-store keeps it out of the cache."""
    try:
        user, temporary_password = user_service.admin_reset_password(
            db, ctx.tenant_id, membership_id, actor_user_id=ctx.user_id
        )
    except user_service.PasswordChangeError as exc:
        return RedirectResponse("/ui/users?error=" + quote(str(exc)), status_code=303)
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="user.password_reset",
        summary=f"Reset password for {user.email} (temporary password issued; change required at next sign-in)",
        resource_type="user",
        resource_id=user.id,
    )
    db.commit()
    response = render_template(
        request, "users/reset_password_result.html", ctx=ctx, email=user.email, temporary_password=temporary_password
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/bulk")
def bulk_upload_form(
    request: Request, ctx: TenantContext = Depends(require_web_permission("user:manage"))
) -> HTMLResponse:
    return render_template(request, "users/bulk_upload.html", ctx=ctx)


@router.post("/bulk")
async def bulk_upload_users(
    request: Request,
    upload_file: UploadFile,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    content = await upload_file.read()

    # Recorded before parsing (and committed on its own) so a rejected/malformed file is
    # still captured for audit purposes, not just successful uploads — see
    # services/bulk_upload_file_service.py.
    upload_record = bulk_upload_file_service.record_uploaded_file(
        db,
        ctx.tenant_id,
        kind=BulkUploadKind.USERS,
        filename=upload_file.filename or "upload.csv",
        content_type=upload_file.content_type,
        data=content,
        uploaded_by_user_id=ctx.user_id,
        customer_id=ctx.customer_id,
    )
    db.commit()

    try:
        rows = parse_tabular_file(upload_file.filename or "upload.csv", content)
    except TabularParseError as exc:
        return render_template(
            request, "users/bulk_upload.html", ctx=ctx, error=str(exc), status_code=422, upload_record=upload_record
        )
    if not rows:
        return render_template(
            request,
            "users/bulk_upload.html",
            ctx=ctx,
            error="That file has no data rows.",
            status_code=422,
            upload_record=upload_record,
        )

    results = user_service.create_users_from_rows(db, ctx.tenant_id, rows)
    for r in results:
        if r.outcome != "created":
            continue
        created_user = UserRepository(db).get_by_email(r.email) if r.email else None
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="user.create",
            summary=f"Added user {r.email} via bulk upload ({r.row_label})",
            resource_type="user",
            resource_id=created_user.id if created_user else None,
        )
        if r.membership_id is not None:
            bulk_upload_reversal_service.track_created_record(
                db, ctx.tenant_id, upload_record.id, resource_type="tenant_membership", resource_id=r.membership_id, row_label=r.row_label
            )
    pending = []
    for r in results:
        if r.outcome != "needs_confirmation":
            continue
        group = security_group_service.get_security_group(db, ctx.tenant_id, r.security_group_id) if r.security_group_id else None
        pending.append(
            {
                "email": r.email,
                "group_name": group.name if group else "",
                "value": f"{r.email}::{r.security_group_id}::",
            }
        )

    bulk_upload_file_service.set_result_counts(
        db,
        upload_record,
        succeeded_count=sum(r.outcome in ("created", "already_member") for r in results),
        failed_count=sum(r.outcome == "failed" for r in results),
    )
    db.commit()
    return render_template(
        request, "users/bulk_result.html", ctx=ctx, results=results, pending=pending, upload_record=upload_record
    )


@router.post("/bulk/confirm")
def bulk_confirm_users(
    request: Request,
    confirm: list[str] = Form([]),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    # "email::security_group_id::customer_id::require_webauthn" — the last segment is
    # only ever populated by /ui/users/access/grant's checkbox; the plain CSV bulk-user
    # upload flow's 3-segment values (no trailing "::flag") parse with it defaulting to
    # False, so both producers of `confirm` values keep working through this one route.
    confirmations: list[tuple[str, uuid.UUID, uuid.UUID | None, bool]] = []
    for value in confirm:
        parts = value.split("::")
        if len(parts) < 2:
            continue
        email, group_id_str = parts[0], parts[1]
        customer_id_str = parts[2] if len(parts) > 2 else ""
        require_webauthn = bool(parts[3]) if len(parts) > 3 else False
        if email and group_id_str:
            confirmations.append(
                (email, uuid.UUID(group_id_str), uuid.UUID(customer_id_str) if customer_id_str else None, require_webauthn)
            )

    results = user_service.confirm_users_bulk(db, ctx.tenant_id, confirmations)
    # confirm_users_bulk already committed each grant individually (per-row isolation,
    # same as every other bulk flow) — these audit entries land in a follow-up commit
    # of their own rather than the same transaction as the grant, the one exception to
    # this feature's usual "same transaction" rule, forced by that pre-existing pattern.
    for r in results:
        if r.outcome != "created":
            continue
        created_user = UserRepository(db).get_by_email(r.email) if r.email else None
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="user.grant_cross_tenant_access",
            summary=f"Granted {r.email} access to this organization (bulk confirm)",
            resource_type="user",
            resource_id=created_user.id if created_user else None,
        )
    db.commit()
    return render_template(request, "users/bulk_result.html", ctx=ctx, results=results, pending=[])


@router.get("/access")
def access_lookup(
    request: Request,
    email: str = "",
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
) -> HTMLResponse:
    rows = user_service.get_access_for_email(db, ctx.tenant_id, email) if email else []
    groups = security_group_service.list_security_groups(db, ctx.tenant_id)
    customers = customer_service.list_customers(db, ctx.tenant_id)
    tenant = db.get(Tenant, ctx.tenant_id)
    password_policy_hint = describe_password_policy(effective_policy(tenant, None))
    return render_template(
        request, "users/access.html", ctx=ctx, email=email, rows=rows, groups=groups, customers=customers,
        password_policy_hint=password_policy_hint,
    )


@router.post("/access/grant")
def access_grant(
    request: Request,
    email: str = Form(...),
    password: str = Form(""),
    security_group_id: uuid.UUID = Form(...),
    customer_ids: list[str] = Form([]),
    require_webauthn: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    groups = security_group_service.list_security_groups(db, ctx.tenant_id)
    customers = customer_service.list_customers(db, ctx.tenant_id)

    if not customer_ids:
        tenant = db.get(Tenant, ctx.tenant_id)
        return render_template(
            request, "users/access.html", ctx=ctx, email=email, rows=user_service.get_access_for_email(db, ctx.tenant_id, email),
            groups=groups, customers=customers, status_code=422,
            error="Select at least one customer (or bank-wide) to grant access to.",
            password_policy_hint=describe_password_policy(effective_policy(tenant, None)),
        )

    resolved_ids: list[uuid.UUID | None] = [uuid.UUID(c) if c else None for c in customer_ids]
    pairs = user_service.grant_multi_customer_access(
        db,
        ctx.tenant_id,
        email=email,
        password=password,
        security_group_id=security_group_id,
        customer_ids=resolved_ids,
        require_webauthn=require_webauthn,
    )

    group = security_group_service.get_security_group(db, ctx.tenant_id, security_group_id)
    customer_names = {c.id: c.name for c in customers}

    results = []
    pending = []
    for customer_id, result in pairs:
        row_label = customer_names.get(customer_id, "—") if customer_id else "bank-wide"
        results.append({"outcome": result.outcome, "row_label": row_label, "error": result.error})
        if result.outcome == "created":
            created_user = UserRepository(db).get_by_email(email)
            audit_log_service.record_action(
                db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="user.create",
                summary=f"Added user {email} ({row_label}, multi-customer grant)", resource_type="user",
                resource_id=created_user.id if created_user else None,
            )
        elif result.outcome == "needs_confirmation":
            flag = "1" if require_webauthn else ""
            pending.append(
                {
                    "email": email,
                    "group_name": group.name if group else "",
                    "value": f"{email}::{security_group_id}::{customer_id or ''}::{flag}",
                }
            )

    db.commit()
    return render_template(request, "users/bulk_result.html", ctx=ctx, results=results, pending=pending, upload_record=None)


# NOTE: /{membership_id} (bare, no suffix) must be registered after every literal path
# above it (/bulk, /bulk/confirm, /confirm) — FastAPI matches path patterns in
# registration order, and "bulk" would otherwise be captured as membership_id and
# rejected as an invalid UUID before those routes are ever tried. Same class of pitfall
# as /bulk vs /{item_id} elsewhere in this codebase.


@router.get("/{membership_id}/edit", dependencies=[Depends(_people_only)])
def edit_user_form(
    request: Request,
    membership_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
) -> HTMLResponse:
    membership = TenantMembershipRepository(db, ctx.tenant_id).get(membership_id)
    if membership is None:
        raise WebNotFound()
    member_user = UserRepository(db).get(membership.user_id)
    groups = security_group_service.list_security_groups(db, ctx.tenant_id)
    customers = customer_service.list_customers(db, ctx.tenant_id)
    access = account_access_service.get_access(db, ctx.tenant_id, membership.id) if membership.customer_id else None
    return render_template(
        request, "users/edit.html", ctx=ctx, membership=membership, member_user=member_user, groups=groups, customers=customers,
        account_access=account_access_service.describe(access) if access else None,
    )


@router.get("/{membership_id}/accounts", dependencies=[Depends(_people_only)])
def account_access_form(
    request: Request, membership_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
) -> HTMLResponse:
    """Which of the customer's accounts this person sees (services/account_access_service.py)."""
    try:
        access = account_access_service.get_access(db, ctx.tenant_id, membership_id)
    except account_access_service.AccountAccessError:
        raise WebNotFound() from None
    return _account_access_page(request, db, ctx, access)


def _account_access_page(request: Request, db: Session, ctx: TenantContext, access, *, error: str | None = None,
                         chosen: set | None = None, status_code: int = 200) -> HTMLResponse:
    return render_template(
        request, "users/accounts.html", ctx=ctx, access=access, member_user=UserRepository(db).get(access.membership.user_id),
        customer=customer_service.get_customer(db, ctx.tenant_id, access.membership.customer_id),
        chosen=chosen if chosen is not None else access.account_ids, error=error, status_code=status_code,
    )


@router.post("/{membership_id}/accounts", dependencies=[Depends(_people_only)])
async def save_account_access(
    request: Request, membership_id: uuid.UUID, db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")), _csrf: None = Depends(verify_csrf),
) -> Response:
    form = await request.form()
    all_accounts = form.get("scope") != "some"
    try:
        account_ids = [uuid.UUID(str(v)) for v in form.getlist("account_id")]
        access = account_access_service.set_access(db, ctx.tenant_id, membership_id, all_accounts=all_accounts, account_ids=account_ids)
    except (account_access_service.AccountAccessError, ValueError) as exc:
        db.rollback()
        try:
            access = account_access_service.get_access(db, ctx.tenant_id, membership_id)
        except account_access_service.AccountAccessError:
            raise WebNotFound() from None
        message = str(exc) if isinstance(exc, account_access_service.AccountAccessError) else "Choose accounts from the list."
        chosen = {str(v) for v in form.getlist("account_id")}
        return _account_access_page(request, db, ctx, access, error=message, status_code=422,
                                    chosen={a.id for a in access.customer_accounts if str(a.id) in chosen})
    member = UserRepository(db).get(access.membership.user_id)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="user.account_access",
        summary=f"Account access for {member.email if member else access.membership.user_id}: {account_access_service.describe(access)}",
        resource_type="user", resource_id=access.membership.user_id,
    )
    db.commit()
    return RedirectResponse("/ui/users?flash=" + quote("Account access saved."), status_code=303)


@router.post("/{membership_id}", dependencies=[Depends(_people_only)])
def update_user(
    request: Request,
    membership_id: uuid.UUID,
    security_group_id: uuid.UUID = Form(...),
    customer_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("user:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    resolved_customer_id = uuid.UUID(customer_id) if customer_id else None
    try:
        membership = user_service.update_membership(
            db, ctx.tenant_id, membership_id, security_group_id=security_group_id, customer_id=resolved_customer_id
        )
    except ValueError as exc:
        db.rollback()
        existing = TenantMembershipRepository(db, ctx.tenant_id).get(membership_id)
        if existing is None:
            raise WebNotFound() from None
        member_user = UserRepository(db).get(existing.user_id)
        groups = security_group_service.list_security_groups(db, ctx.tenant_id)
        customers = customer_service.list_customers(db, ctx.tenant_id)
        return render_template(
            request,
            "users/edit.html",
            ctx=ctx,
            membership=existing,
            member_user=member_user,
            groups=groups,
            customers=customers,
            error=str(exc),
            status_code=422,
        )

    actor = UserRepository(db).get(membership.user_id)
    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="user.update_membership",
        summary=f"Updated entitlements for {actor.email if actor else membership.user_id}",
        resource_type="user",
        resource_id=membership.user_id,
    )
    db.commit()
    return RedirectResponse("/ui/users?flash=Entitlements+updated.", status_code=303)
