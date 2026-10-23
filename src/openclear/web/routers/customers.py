# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.tenant import Tenant
from openclear.services import account_service, audit_log_service, auto_disposition_service, customer_service, sso_service
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.form_errors import friendly_error
from openclear.web.redirects import see_other
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/customers", tags=["web-customers"])


def _input_from_form(
    customer_number: str,
    name: str,
    external_customer_id: str,
    tax_id: str,
    primary_contact_name: str,
    email: str,
    phone: str,
    website: str,
    address_line1: str,
    address_line2: str,
    city: str,
    state: str,
    postal_code: str,
    notes: str,
) -> customer_service.CustomerInput:
    return customer_service.CustomerInput(
        customer_number=customer_number,
        name=name,
        external_customer_id=external_customer_id or None,
        tax_id=tax_id or None,
        primary_contact_name=primary_contact_name or None,
        email=email or None,
        phone=phone or None,
        website=website or None,
        address_line1=address_line1 or None,
        address_line2=address_line2 or None,
        city=city or None,
        state=state or None,
        postal_code=postal_code or None,
        notes=notes or None,
    )


HUB_PAGE_SIZE = 50
QUIET_CHOICES = (7, 14, 30, 60)


@router.get("")
def list_customers(
    request: Request, page: int = 1, q: str = "", quiet: str = "", db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
) -> HTMLResponse:
    """The customer hub (UX_PLAN.md U7; services/customer_hub_service.py): needs-action first."""
    from datetime import datetime, timezone

    from openclear.services import customer_hub_service

    quiet_days = int(quiet) if quiet.isdigit() else None
    rows = customer_hub_service.rows(db, ctx.tenant_id, q=q, quiet_days=quiet_days)
    pages = max(1, -(-len(rows) // HUB_PAGE_SIZE))
    page = min(max(1, page), pages)
    return render_template(request, "customers/list.html", ctx=ctx, rows=rows[(page - 1) * HUB_PAGE_SIZE: page * HUB_PAGE_SIZE],
                           total=len(rows), page=page, pages=pages, q=q, quiet=quiet_days, quiet_choices=QUIET_CHOICES,
                           now=datetime.now(timezone.utc))


@router.post("/{customer_id}/remind")
def remind_customer(
    customer_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("customer:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """"Send a reminder now": one message to each person who can act on the customer's open items."""

    from openclear.services import reminder_service

    customer = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    items, people = reminder_service.remind_customer_now(db, ctx.tenant_id, customer_id)
    if items:
        audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="customer.remind",
                                        summary=f"Reminded {customer.name} of {items} open item{'s' if items != 1 else ''} ({people} people)",
                                        resource_type="customer", resource_id=customer.id)
    db.commit()
    message = (f"Reminder sent to {people} {'person' if people == 1 else 'people'} about {items} open item{'s' if items != 1 else ''}."
               if people else ("Nobody at this customer can act on its items." if items else "This customer has nothing open."))
    return see_other(f"/ui/customers/{customer_id}", flash=message)


@router.get("/new")
def new_customer_form(
    request: Request, ctx: TenantContext = Depends(require_web_permission("customer:manage"))
) -> HTMLResponse:
    return render_template(request, "customers/form.html", ctx=ctx, customer=None)


@router.post("")
def create_customer(
    request: Request,
    customer_number: str = Form(...),
    name: str = Form(...),
    external_customer_id: str = Form(""),
    tax_id: str = Form(""),
    primary_contact_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    website: str = Form(""),
    address_line1: str = Form(""),
    address_line2: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    postal_code: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    data = _input_from_form(
        customer_number, name, external_customer_id, tax_id, primary_contact_name, email, phone, website,
        address_line1, address_line2, city, state, postal_code, notes,
    )
    try:
        customer = customer_service.create_customer(db, ctx.tenant_id, data)
    except Exception as exc:  # noqa: BLE001 — surface a DB constraint violation (e.g. duplicate customer number) to the form
        db.rollback()
        return render_template(
            request, "customers/form.html", ctx=ctx, customer=None, error=friendly_error(exc, action="Could not create customer", duplicate="A customer with that customer number already exists."), status_code=422
        )

    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="customer.create",
        summary=f"Added customer {customer.customer_number} ({customer.name})",
        resource_type="customer",
        resource_id=customer.id,
    )
    db.commit()
    # Straight into this customer's own setup checklist rather than the plain list —
    # see services/wizard_service.py.
    return see_other(f"/ui/customers/{customer.id}/wizard", flash="Customer created.")


# NOTE: /new must be registered before /{customer_id} below — FastAPI matches path
# patterns in registration order, and "new" would otherwise be captured as customer_id
# and rejected as an invalid UUID before this route is ever tried.


@router.get("/deleted")
def deleted_customers(request: Request, db: Session = Depends(get_db),
                      ctx: TenantContext = Depends(require_web_permission("customer:manage"))) -> HTMLResponse:
    """Customers whose data was deleted after their last service day, with their certificates
    (plans/CUSTOMER_OFFBOARDING_PLAN.md)."""
    from openclear.services import customer_offboarding_service

    return render_template(request, "customers/deleted.html", ctx=ctx, deletions=customer_offboarding_service.deletions(db, ctx.tenant_id))


@router.get("/deleted/{deletion_id}/certificate")
def deletion_certificate(deletion_id: uuid.UUID, db: Session = Depends(get_db),
                         ctx: TenantContext = Depends(require_web_permission("customer:manage"))):
    import json

    from fastapi.responses import Response

    from openclear.domain.bank_deletion import CustomerDeletion

    record = db.get(CustomerDeletion, deletion_id)
    if record is None or record.tenant_id != ctx.tenant_id:
        raise WebNotFound()
    return Response(json.dumps(record.certificate, indent=2, sort_keys=True), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="openclear-customer-deletion-{record.customer_number}.json"'})


@router.get("/{customer_id}")
def customer_detail(
    request: Request,
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
) -> HTMLResponse:
    customer = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    accounts = account_service.list_accounts(db, ctx.tenant_id, customer_id=customer.id)
    disposition_summary = (
        auto_disposition_service.get_disposition_summary(db, ctx.tenant_id, customer.id) if "admin:manage" in ctx.permissions else None
    )
    sso_connection_count = len(sso_service.list_connections(db, ctx.tenant_id, customer_id=customer.id))
    from openclear.services import exception_service, reminder_service

    open_count, nearest_deadline = exception_service.attention_summary(db, ctx.tenant_id, customer_id=customer.id)
    tenant = db.get(Tenant, ctx.tenant_id)
    try:
        reminder_timing = reminder_service.describe(reminder_service.customer_offsets(tenant, customer))
    except reminder_service.ReminderError:
        reminder_timing = "Not valid"
    from openclear.domain.user import User
    from openclear.services import customer_people_service

    admins = [db.get(User, m.user_id).email for m in customer_people_service.administrators(db, ctx.tenant_id, customer.id)]
    from openclear.networks.registry import registered_codes
    from openclear.services import cutoff_service

    return render_template(
        request, "customers/detail.html", ctx=ctx, customer=customer, accounts=accounts, disposition_summary=disposition_summary,
        sso_connection_count=sso_connection_count, open_count=open_count, nearest_deadline=nearest_deadline,
        reminder_timing=reminder_timing, customer_admins=admins,
        # What applies: the bank's own, else the installation's default (every bank has a cut-off).
        bank_cutoffs={code: cutoff_service.cutoff_for(db, tenant, None, code) for code in registered_codes()},
        bank_time_zone=cutoff_service.time_zone_for(tenant),
        customer_cutoffs=cutoff_service.customer_cutoffs(db, ctx.tenant_id, customer.id), networks=registered_codes(),
        offered_group_count=len(customer_people_service.offered_groups(db, ctx.tenant_id)),
        **_offboarding_context(customer, tenant),
    )


def _offboarding_context(customer, tenant) -> dict:
    from datetime import date, timedelta
    from zoneinfo import ZoneInfo

    from openclear.services import customer_offboarding_service

    last_day = None
    if customer.service_ends_at is not None:
        ends = customer.service_ends_at
        ends = ends.replace(tzinfo=ZoneInfo("UTC")) if ends.tzinfo is None else ends
        last_day = (ends.astimezone(ZoneInfo(tenant.time_zone or "UTC")) - timedelta(seconds=1)).date().isoformat()
    return {"offboarding_deletion_due": customer_offboarding_service.deletion_due(customer), "offboarding_last_day": last_day,
            "retention_days": customer_offboarding_service.retention_days(), "today": date.today().isoformat()}


@router.post("/{customer_id}/offboarding")
def offboarding(customer_id: uuid.UUID, step: str = Form(...), last_service_day: str = Form(""), reason: str = Form(""),
                db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("customer:manage")),
                _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """Ending a customer's positive pay on its last service day, and its legal hold
    (plans/CUSTOMER_OFFBOARDING_PLAN.md). Ending it, restoring it once ended, and taking the
    hold off each need a second administrator."""
    from openclear.services import change_request_service
    from openclear.web.routers.tenant_settings import waiting_message

    customer = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    target = {"customer_id": str(customer.id)}
    if step == "schedule":
        kind, payload, summary = "customer.offboard", {**target, "last_service_day": last_service_day.strip()}, \
            f"End positive pay for {customer.name} on {last_service_day.strip()}"
    elif step == "stop":
        kind, payload, summary = "customer.offboard", {**target, "last_service_day": None}, f"Keep {customer.name} on positive pay"
    elif step == "hold":
        kind, payload, summary = "customer.legal_hold", {**target, "held": True, "reason": reason.strip()[:500]}, \
            f"Put {customer.name}'s data on legal hold: {reason.strip()[:300]}"
    elif step == "release":
        kind, payload, summary = "customer.legal_hold", {**target, "held": False}, f"Take {customer.name}'s data off legal hold"
    else:
        return see_other(f"/ui/customers/{customer_id}#offboarding")
    try:
        outcome = change_request_service.request_or_apply(db, kind, payload, tenant_id=ctx.tenant_id, user_id=ctx.user_id, summary=summary)
    except ValueError as exc:
        db.rollback()
        return see_other(f"/ui/customers/{customer_id}#offboarding", error=str(exc))
    db.commit()
    if outcome.waiting:
        return see_other(f"/ui/customers/{customer_id}#offboarding", flash=waiting_message(outcome))
    return see_other(f"/ui/customers/{customer_id}#offboarding", flash=outcome.summary)


@router.post("/{customer_id}/self-service")
def set_self_service(
    customer_id: uuid.UUID,
    manages_users: bool = Form(False),
    manages_sso: bool = Form(False),
    manages_api_clients: bool = Form(False),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Lets the customer's own administrators manage its people, sign-on and API clients, or
    stops them at once (CUSTOMER_ADMIN_PLAN.md; MOBILE_PLAN.md R4)."""

    from openclear.services import change_request_service, customer_service
    from openclear.web.routers.tenant_settings import waiting_message

    customer = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    try:
        # Giving a customer more to manage itself waits for a second admin when the bank asks (DUAL_CONTROL_PLAN.md).
        outcome = change_request_service.request_or_apply(
            db, "customer.self_service", {"customer_id": str(customer_id), "users": manages_users, "sso": manages_sso,
                                          "api_clients": manages_api_clients},
            tenant_id=ctx.tenant_id, user_id=ctx.user_id, summary=f"Let {customer.name} manage more of its own access")
    except ValueError as exc:
        db.rollback()
        return see_other(f"/ui/customers/{customer_id}", error=str(exc))
    if outcome.waiting:
        db.commit()
        return see_other(f"/ui/customers/{customer_id}", flash=waiting_message(outcome))
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="customer.self_service",
        summary=f"{customer.name} manages its own people: {'yes' if manages_users else 'no'}; its own sign-on: {'yes' if manages_sso else 'no'}; "
                f"its own API clients: {'yes' if manages_api_clients else 'no'}",
        resource_type="customer", resource_id=customer.id,
    )
    db.commit()
    return see_other(f"/ui/customers/{customer.id}", flash="Self-service saved.")


@router.post("/{customer_id}/cutoff")
async def set_cutoff(request: Request, customer_id: uuid.UUID, db: Session = Depends(get_db),
                     ctx: TenantContext = Depends(require_web_permission("tenant:manage")), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """A cut-off agreed with this customer, per network, in place of the bank's (UI_PLAN.md
    UI-D13). A protected change: it moves when this customer's decisions become final."""
    from openclear.networks.registry import registered_codes
    from openclear.services import change_request_service, customer_service
    from openclear.web.routers.tenant_settings import waiting_message

    customer = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    form = await request.form()
    cutoffs = {code: (str(form.get(f"cutoff_{code}") or "").strip() or None) for code in registered_codes()}
    try:
        outcome = change_request_service.request_or_apply(
            db, "customer_cutoff", {"customer_id": str(customer.id), "cutoffs": cutoffs}, tenant_id=ctx.tenant_id, user_id=ctx.user_id,
            summary=f"Change the cut-off agreed with {customer.name}")
    except ValueError as exc:
        db.rollback()
        return see_other(f"/ui/customers/{customer_id}", error=str(exc))
    if outcome.waiting:
        db.commit()
        return see_other(f"/ui/customers/{customer_id}", flash=waiting_message(outcome))
    if outcome.summary != "Nothing changed.":
        audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="customer.update_cutoff",
                                        summary=outcome.summary, resource_type="customer", resource_id=customer.id)
    db.commit()
    return see_other(f"/ui/customers/{customer.id}", flash="Cut-off saved.")


@router.post("/{customer_id}/invite")
def invite_person(request: Request, customer_id: uuid.UUID, email: str = Form(""), return_to: str = Form("", alias="return"),
                  db: Session = Depends(get_db), ctx: TenantContext = Depends(require_web_permission("user:manage")),
                  _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """The bank invites one of this customer's people, with no access (UI_PLAN.md UI-D14):
    signed in, nothing else, until someone other than this person gives them a role."""
    from openclear.config import get_settings
    from openclear.services import customer_people_service, customer_service
    from openclear.web.redirects import checklist_return

    customer = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    back = checklist_return(return_to) or f"/ui/customers/{customer.id}"
    try:
        invitation = customer_people_service.invite_without_access(
            db, ctx.tenant_id, customer.id, email=email, invited_by_user_id=ctx.user_id,
            accept_base_url=get_settings().public_base_url or str(request.base_url))
    except customer_people_service.PeopleError as exc:
        db.rollback()
        return see_other(back, error=str(exc))
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="customer_people.invite",
                                    summary=f"Invited {invitation.email} to {customer.name}, with no access yet",
                                    resource_type="customer", resource_id=customer.id)
    db.commit()
    return see_other(back, flash=f"Invitation sent to {invitation.email}. Once they've joined, someone other than you gives them a role under Users.")


@router.post("/{customer_id}/approval")
def set_approval(
    customer_id: uuid.UUID,
    approve_users: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Whether new people this customer's side adds wait for the bank (MOBILE_PLAN.md R6): the
    bank's setting, or this customer's own."""
    customer = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    value = {"": None, "true": True, "false": False}.get(approve_users)
    customer.approve_users = value
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="customer.approval",
        summary=f"New people {customer.name} adds: " + {None: "as the bank's setting", True: "wait for the bank", False: "don't wait"}[value],
        resource_type="customer", resource_id=customer.id,
    )
    db.commit()
    return see_other(f"/ui/customers/{customer.id}", flash="Approval setting saved.")


@router.get("/{customer_id}/edit")
def edit_customer_form(
    request: Request,
    customer_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
) -> HTMLResponse:
    customer = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if customer is None:
        raise WebNotFound()
    return render_template(request, "customers/form.html", ctx=ctx, customer=customer)


@router.post("/{customer_id}")
def update_customer(
    request: Request,
    customer_id: uuid.UUID,
    customer_number: str = Form(...),
    name: str = Form(...),
    external_customer_id: str = Form(""),
    tax_id: str = Form(""),
    primary_contact_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    website: str = Form(""),
    address_line1: str = Form(""),
    address_line2: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    postal_code: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("customer:manage")),
    _csrf: None = Depends(verify_csrf),
) -> HTMLResponse:
    data = _input_from_form(
        customer_number, name, external_customer_id, tax_id, primary_contact_name, email, phone, website,
        address_line1, address_line2, city, state, postal_code, notes,
    )
    existing = customer_service.get_customer(db, ctx.tenant_id, customer_id)
    if existing is None:
        raise WebNotFound()

    try:
        customer = customer_service.update_customer(db, ctx.tenant_id, customer_id, data)
    except Exception as exc:  # noqa: BLE001 — surface a DB constraint violation (e.g. duplicate customer number) to the form
        db.rollback()
        return render_template(
            request, "customers/form.html", ctx=ctx, customer=existing, error=friendly_error(exc, action="Could not update customer", duplicate="Another customer already has that customer number."), status_code=422
        )

    audit_log_service.record_action(
        db,
        ctx.tenant_id,
        actor_user_id=ctx.user_id,
        channel="web",
        action="customer.update",
        summary=f"Updated customer {customer.customer_number} ({customer.name})",
        resource_type="customer",
        resource_id=customer.id,
    )
    db.commit()
    return see_other(f"/ui/customers/{customer_id}", flash="Customer updated.")
