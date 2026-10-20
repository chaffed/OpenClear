# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.issued_item import IssuedItemStatus
from openclear.domain.stop_payment import StopPaymentStatus
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.repositories.issued_item_repo import IssuedItemRepository
from openclear.repositories.stop_payment_repo import StopPaymentRepository
from openclear.services import exception_service, security_group_service, wizard_service
from openclear.web.deps import get_web_context, render_template
from openclear.web.redirects import see_other

router = APIRouter(prefix="/ui", tags=["web-dashboard"])


def _age(since: datetime) -> str:
    if since.tzinfo is None:  # SQLite drops tzinfo; stored values are UTC
        since = since.replace(tzinfo=timezone.utc)
    hours = int((datetime.now(timezone.utc) - since).total_seconds() // 3600)
    if hours < 1:
        return "Under 1 hour"
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{hours // 24} days"


def _attention_summary(db: Session, ctx: TenantContext) -> dict:
    """What the exception work queue looks like right now, in the viewer's scope: how many
    items need a person, how many are due within a day (or overdue), and how long the
    oldest has been waiting."""
    waiting = ExceptionRepository(db, ctx.tenant_id, ctx.customer_id).query().where(
        ExceptionItem.status.in_(exception_service.NEEDS_ATTENTION)
    ).subquery()
    count, oldest = db.execute(select(func.count(), func.min(waiting.c.created_at)).select_from(waiting)).one()
    due_soon = db.execute(
        select(func.count()).select_from(waiting).where(
            waiting.c.decision_deadline.is_not(None),
            waiting.c.decision_deadline <= datetime.now(timezone.utc) + timedelta(hours=24),
        )
    ).scalar_one()
    return {"count": count, "due_soon": due_soon, "oldest_age": _age(oldest) if oldest else None}


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context)):
    # "Decide now" (UX_PLAN.md U5): a customer's people with checks waiting for their
    # decision land on those, not on the dashboard. The navigation's own Dashboard link
    # (?home=1) still opens it.
    if (ctx.customer_id is not None and ctx.needs_attention_count and not request.query_params.get("home")
            and ({"exception:decide", "exception:recommend"} & set(ctx.permissions))):
        return see_other("/ui/exceptions")
    # Only bank-wide admins can act on the bank wizard's steps, and customer-scoped
    # sessions don't have a single tenant-wide "getting started" state to show anyway.
    show_getting_started = (
        ctx.customer_id is None
        and "tenant:manage" in ctx.permissions
        and not wizard_service.is_bank_wizard_complete(db, ctx.tenant_id)
    )
    # TenantContext carries security_group_id (a UUID), not a display name — this is the
    # one place the dashboard needs the human-readable name, so resolve it here rather
    # than growing TenantContext for a single label.
    group = security_group_service.get_security_group(db, ctx.tenant_id, ctx.security_group_id)

    # Each count is gated on the same read permission its own list page requires, so a
    # role that can't see a resource at all doesn't get a stat card teasing its count
    # either — mirrors the "Admin" quick-link's existing can(ctx, ...) gating below.
    attention = None
    if "exception:read" in ctx.permissions:
        attention = _attention_summary(db, ctx)
    outstanding_issued_items_count = None
    if "issued_item:read" in ctx.permissions:
        outstanding_issued_items_count = IssuedItemRepository(db, ctx.tenant_id, ctx.customer_id).count(
            status=IssuedItemStatus.OUTSTANDING
        )
    active_stop_payments_count = None
    if "stop_payment:read" in ctx.permissions:
        active_stop_payments_count = StopPaymentRepository(db, ctx.tenant_id, ctx.customer_id).count(
            status=StopPaymentStatus.ACTIVE
        )

    return render_template(
        request, "dashboard.html", ctx=ctx, show_getting_started=show_getting_started,
        security_group_name=group.name if group else None,
        attention=attention,
        awaiting_my_approval=ctx.pending_approval_count if "exception:decide" in ctx.permissions else None,
        outstanding_issued_items_count=outstanding_issued_items_count,
        active_stop_payments_count=active_stop_payments_count,
    )
