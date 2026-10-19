# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Access reviews (CONTROLS_PLAN.md CT3; services/access_review_service.py): the bank's of every
membership (user:manage), or a self-managing customer's of its own people (customer_user:manage).
The same pages serve both; each sees only its own reviews."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.services import access_review_service, audit_log_service
from openclear.services.access_review_service import ReviewError
from openclear.web.deps import WebForbidden, WebNotFound, render_template, require_any_web_permission
from openclear.web.security import verify_csrf
from openclear.web.redirects import see_other

router = APIRouter(prefix="/ui/access-reviews", tags=["web-access-reviews"])
BASE = "/ui/access-reviews"


def _reviewer(ctx: TenantContext = Depends(require_any_web_permission("user:manage", "customer_user:manage"))) -> TenantContext:
    needed = "customer_user:manage" if ctx.customer_id is not None else "user:manage"
    if needed not in ctx.permissions:
        raise WebForbidden()
    return ctx


def _record(db: Session, ctx: TenantContext, action: str, summary: str, review, metadata: dict | None = None) -> None:
    audit_log_service.record_action(db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action=action, summary=summary,
                                     resource_type="access_review", resource_id=review.id, metadata=metadata)


def _review(db: Session, ctx: TenantContext, review_id: uuid.UUID):
    found = access_review_service.get_review(db, ctx.tenant_id, ctx.customer_id, review_id)
    if found is None:
        raise WebNotFound()
    return found


def _back(path: str, message: str) -> RedirectResponse:
    return see_other(f"{path}?flash={quote(message)}")


@router.get("")
def list_reviews(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(_reviewer)) -> HTMLResponse:
    return render_template(request, "access_reviews/list.html", ctx=ctx,
                           reviews=access_review_service.reviews(db, ctx.tenant_id, ctx.customer_id),
                           open_review=access_review_service.open_review(db, ctx.tenant_id, ctx.customer_id))


@router.post("")
def start_review(db: Session = Depends(get_db), ctx: TenantContext = Depends(_reviewer), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    try:
        review = access_review_service.start(db, ctx.tenant_id, ctx.customer_id, user_id=ctx.user_id)
    except ReviewError as exc:
        db.rollback()
        return _back(BASE, str(exc))
    _record(db, ctx, "access_review.started", f"Started an access review of {review.item_count} memberships"
            + (f" at {ctx.customer_name}" if ctx.customer_id else ""), review)
    db.commit()
    return see_other(f"{BASE}/{review.id}")


@router.get("/{review_id}")
def show_review(request: Request, review_id: uuid.UUID, db: Session = Depends(get_db), ctx: TenantContext = Depends(_reviewer)) -> HTMLResponse:
    review = _review(db, ctx, review_id)
    rows = access_review_service.items(db, review)
    return render_template(request, "access_reviews/review.html", ctx=ctx, review=review, rows=rows, tally=access_review_service.tally(rows))


@router.post("/{review_id}/items/{item_id}")
def decide(review_id: uuid.UUID, item_id: uuid.UUID, decision: str = Form(""), note: str = Form(""), db: Session = Depends(get_db),
           ctx: TenantContext = Depends(_reviewer), _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    review = _review(db, ctx, review_id)
    path = f"{BASE}/{review.id}"
    try:
        item = access_review_service.decide(db, review, item_id, decision, note=note, user_id=ctx.user_id)
    except ReviewError as exc:
        db.rollback()
        return _back(path, str(exc))
    if item.decision == "removed":
        _record(db, ctx, "access_review.removed", f"Removed {item.who}'s access ({item.role}, {item.scope}) in an access review", review,
                {"membership_id": str(item.membership_id)})
    if access_review_service.complete_if_done(db, review, user_id=ctx.user_id):
        counts = access_review_service.tally(access_review_service.items(db, review))
        _record(db, ctx, "access_review.completed",
                f"Completed an access review of {review.item_count} memberships: {counts['confirmed']} confirmed, {counts['changed']} changed, "
                f"{counts['removed']} removed", review, {"decisions_sha256": review.decisions_sha256})
        db.commit()
        return _back(path, "Review complete: every membership decided.")
    db.commit()
    return _back(path, {"confirmed": "Confirmed.", "changed": "Marked changed.", "removed": "Access removed."}[item.decision])
