# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.customer import Customer
from openclear.domain.decision import DecisionOutcome
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.domain.tenant import Tenant
from openclear.domain.user import User
from openclear.networks.registry import get_adapter
from openclear.repositories.account_repo import AccountRepository
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import (
    ach_return_reason_service,
    audit_log_service,
    decision_service,
    exception_evidence,
    exception_service,
)
from openclear.services import check_return_reason_service
from openclear.services.decision_service import DecisionError
from openclear.web.deps import WebNotFound, render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.security import verify_csrf

router = APIRouter(prefix="/ui/exceptions", tags=["web-exceptions"])

_DECISION_ERROR_MESSAGES = {
    DecisionError.NOT_FOUND: "Exception not found.",
    DecisionError.ALREADY_DECIDED: "This exception has already been decided.",
    DecisionError.RECOMMENDATION_REQUIRED: "A recommendation is required before this can be decided under dual control.",
    DecisionError.MAKER_CANNOT_APPROVE_OWN_RECOMMENDATION: "You recommended this decision — a different user must approve it.",
    DecisionError.WITHDRAWN: "This exception was withdrawn because its underlying item was backed out.",
    DecisionError.RETURN_REASON_REQUIRED: "Select a return reason from the list — freeform text isn't accepted for ACH returns.",
    DecisionError.INVALID_RETURN_REASON: "That return reason no longer exists or has been deactivated — pick another.",
}


_CHOOSE_OUTCOME = "Choose Pay or Return."


def _parse_outcome(raw: str) -> DecisionOutcome | None:
    """The forms start with no outcome selected (FIX_PLAN.md Phase 6, U3) so nobody pays an
    item by accident. An empty or unknown value gets a clear message, not a raw
    validation error."""
    try:
        return DecisionOutcome(raw)
    except ValueError:
        return None


def _parse_optional_uuid(raw: str) -> uuid.UUID | None:
    return uuid.UUID(raw) if raw else None


PAY_REASON = "Reviewed and paid"
_CHOOSE_RETURN_REASON = "Choose why it's being returned."


def _reason_for(db: Session, ctx: TenantContext, exception_id: uuid.UUID, outcome: DecisionOutcome, reason_code: str,
                raw_reason_id: str) -> tuple[uuid.UUID | None, str, str | None]:
    """(return reason id, reason text, problem). Paying needs no reason (UX_PLAN.md U4);
    returning from a web page needs one from the bank's list for the item's network."""
    reason_id = _parse_optional_uuid(raw_reason_id)
    if outcome == DecisionOutcome.PAY:
        return None, reason_code.strip() or PAY_REASON, None
    if reason_id is None and not reason_code.strip():
        return None, reason_code, _CHOOSE_RETURN_REASON
    return reason_id, reason_code.strip(), None


def _summarize_source_item(network_code: str, source_item: Any) -> dict[str, Any]:
    """The exceptions queue/detail is network-agnostic, but a reviewer still needs to see
    what the underlying check/ACH transaction actually was — this builds a small display
    dict from whichever concrete row networks.registry.get_adapter().load_source_item()
    returned, so the template never needs to branch on network-specific field names."""
    if network_code == "check":
        return {
            "label": f"Check #{source_item.check_number}",
            "amount": source_item.presented_amount,
            "date": str(source_item.presented_date),
        }
    if network_code == "ach":
        return {
            "label": f"{source_item.originator_name} ({source_item.originator_id})",
            "amount": source_item.amount,
            "date": str(source_item.effective_date),
        }
    return {"label": str(source_item.id), "amount": None, "date": ""}


# The queue's status filter: "" (the default) = everything still waiting on a person,
# "all" = every status, or one specific ExceptionStatus value.
_ALL_STATUSES = "all"


def _names_for_page(db: Session, ctx: TenantContext, items: list[ExceptionItem], sources: dict) -> tuple[dict, dict]:
    """Account and customer names for one page of the queue, fetched in two queries rather
    than one per row, through the viewer's own scope."""
    account_ids = {getattr(sources.get(i.id), "account_id", None) for i in items} - {None}
    accounts = {a.id: a for a in AccountRepository(db, ctx.tenant_id, ctx.customer_id).get_many(account_ids)}
    customer_ids = {i.customer_id for i in items} - {None}
    customers = {
        c.id: c.name for c in db.execute(
            select(Customer).where(Customer.tenant_id == ctx.tenant_id, Customer.id.in_(customer_ids))
        ).scalars()
    } if customer_ids and ctx.customer_id is None else {}
    return accounts, customers


def _load_sources(db: Session, items: list[ExceptionItem]) -> dict[uuid.UUID, Any]:
    """Each exception's source item (paid item, ACH transaction...), keyed by exception id:
    one query per network for the whole page, not one per row."""
    by_network: dict[str, list[uuid.UUID]] = {}
    for item in items:
        by_network.setdefault(item.network_code, []).append(item.source_item_id)
    loaded = {code: get_adapter(code).load_source_items(db, ids) for code, ids in by_network.items()}
    return {item.id: loaded[item.network_code].get(item.source_item_id) for item in items}


@router.get("")
def list_exceptions(
    request: Request,
    network_code: str | None = None,
    status: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:read")),
):
    """Defaults to what still needs a person (open, or awaiting approval), oldest first:
    a work queue. Choosing a decided status, or "All", shows newest first instead."""
    if status == _ALL_STATUSES:
        statuses, oldest_first = None, False
    elif status in {s.value for s in ExceptionStatus}:
        statuses = (ExceptionStatus(status),)
        oldest_first = statuses[0] in exception_service.NEEDS_ATTENTION
    else:
        status, statuses, oldest_first = "", exception_service.NEEDS_ATTENTION, True

    from openclear.web import list_search

    filters = dict(network_code=network_code or None, statuses=statuses, customer_id=ctx.customer_id)
    search = list_search.parse(request, extra=("status", "network_code"))
    spec = list_search.exceptions_spec()
    # A work queue (open or awaiting approval) is oldest first, unless another sort is chosen.
    spec.default_order = lambda: [ExceptionItem.created_at.asc() if oldest_first else ExceptionItem.created_at.desc()]
    page_obj = list_search.page_or_export(
        request=request, db=db, ctx=ctx, search=search, spec=spec, page=page, list_name="exceptions",
        count=lambda where: exception_service.count_exceptions(db, ctx.tenant_id, **filters, where=where),
        fetch=lambda **kw: exception_service.list_exceptions(db, ctx.tenant_id, **filters, **kw),
    )
    if not hasattr(page_obj, "items"):
        return page_obj
    # Per-row enrichment only ever runs over the current page's rows.
    sources = _load_sources(db, page_obj.items)
    accounts, customers = _names_for_page(db, ctx, page_obj.items, sources)
    rows = []
    for item in page_obj.items:
        source_item = sources[item.id]
        account = accounts.get(getattr(source_item, "account_id", None))
        rows.append(
            {
                "exception": item,
                "summary": _summarize_source_item(item.network_code, source_item) if source_item else {"label": "(missing)", "amount": None, "date": ""},
                "account": f"{account.account_number} ({account.name})" if account else "",
                "customer": customers.get(item.customer_id, ""),
            }
        )
    return render_template(
        request, "exceptions/list.html", ctx=ctx, rows=rows, page_obj=page_obj, network_filter=network_code,
        status_filter=status, statuses=[s.value for s in ExceptionStatus], search=search,
        can_act=bool({"exception:decide", "exception:recommend"} & set(ctx.permissions)),
        bulk_check_reasons=check_return_reason_service.list_check_return_reasons(db, ctx.tenant_id),
        bulk_ach_reasons=ach_return_reason_service.list_ach_return_reasons(db, ctx.tenant_id, active_only=True),
        show_scores=any(r["exception"].ml_score is not None for r in rows),
        show_deadlines=any(r["exception"].decision_deadline is not None for r in rows),
        show_customers=ctx.customer_id is None and any(r["customer"] for r in rows),
    )


@router.get("/approvals")
def list_approvals(
    request: Request,
    network_code: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:decide")),
) -> HTMLResponse:
    """A dedicated queue for checkers -- everything currently pending_approval, with the
    maker's own recommendation shown inline so a checker doesn't have to open each item
    just to see what's being asked of them. Split out from the general Exceptions list
    (which mixes every status together, and whose detail page used to show the maker's
    "Recommend" form and the checker's "Decide" form stacked together with no indication
    of who recommended what) specifically because that combined view is what made the
    maker/checker workflow feel cumbersome -- see exception_detail's decide/recommend
    split below for the other half of this fix. Registered before /{exception_id} on
    purpose -- FastAPI resolves that path param's uuid.UUID conversion during request
    handling, not route matching, so "approvals" would otherwise 422 against it instead
    of falling through to this route."""
    page_obj = paginate(
        page=page,
        count_fn=lambda: ExceptionRepository(db, ctx.tenant_id, ctx.customer_id).count(
            network_code=network_code, status=ExceptionStatus.PENDING_APPROVAL
        ),
        list_fn=lambda **kw: exception_service.list_exceptions(
            db, ctx.tenant_id, network_code=network_code, status=ExceptionStatus.PENDING_APPROVAL,
            customer_id=ctx.customer_id,
            # Oldest-waiting-first, not newest-first like the general list -- this is a
            # queue meant to be worked through, not a feed of what just happened.
            order_by=ExceptionItem.created_at.asc(), **kw,
        ),
    )
    recommender_ids = {item.recommended_by_user_id for item in page_obj.items if item.recommended_by_user_id}
    recommender_emails = {
        u.id: u.email for u in db.execute(select(User).where(User.id.in_(recommender_ids))).scalars()
    } if recommender_ids else {}

    sources = _load_sources(db, page_obj.items)
    rows = []
    for item in page_obj.items:
        source_item = sources[item.id]
        rows.append(
            {
                "exception": item,
                "summary": _summarize_source_item(item.network_code, source_item) if source_item else {"label": "(missing)", "amount": None, "date": ""},
                "recommended_by_email": recommender_emails.get(item.recommended_by_user_id),
                "is_own_recommendation": item.recommended_by_user_id == ctx.user_id,
            }
        )
    return render_template(
        request, "exceptions/approvals.html", ctx=ctx, rows=rows, page_obj=page_obj, network_filter=network_code
    )


@router.get("/{exception_id}")
def exception_detail(
    request: Request,
    exception_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:read")),
) -> HTMLResponse:
    item = exception_service.get_exception(db, ctx.tenant_id, exception_id, customer_id=ctx.customer_id)
    if item is None:
        raise WebNotFound()

    adapter = get_adapter(item.network_code)
    source_item = adapter.load_source_item(db, item.source_item_id)
    summary = _summarize_source_item(item.network_code, source_item) if source_item else None
    decision = decision_service.get_decision_for_exception(db, ctx.tenant_id, exception_id, customer_id=ctx.customer_id)
    ach_return_reasons = (
        ach_return_reason_service.list_ach_return_reasons(db, ctx.tenant_id, active_only=True)
        if item.network_code == "ach"
        else []
    )
    # The bank's reasons for returning this kind of item (UX_PLAN.md U4).
    return_reasons = ach_return_reasons if item.network_code == "ach" else (
        check_return_reason_service.list_check_return_reasons(db, ctx.tenant_id) if item.network_code == "check" else [])
    recommended_by = db.get(User, item.recommended_by_user_id) if item.recommended_by_user_id else None
    # Lets the "Approve"/"Revise recommendation" forms pre-select the return reason the
    # maker chose. Recommendations made before the id was stored fall back to matching the
    # stored reason text against the live catalog.
    recommended_ach_return_reason_id = item.recommended_ach_return_reason_id or next(
        (r.id for r in ach_return_reasons if r.reason_text == item.recommended_reason_code), None
    )
    tenant = db.get(Tenant, ctx.tenant_id)
    evidence = exception_evidence.build_evidence(db, item, source_item, scope_customer_id=ctx.customer_id)

    return render_template(
        request,
        "exceptions/detail.html",
        ctx=ctx,
        item=item,
        evidence=evidence,
        summary=summary,
        decision=decision,
        ach_return_reasons=ach_return_reasons,
        recommended_by=recommended_by,
        recommended_ach_return_reason_id=recommended_ach_return_reason_id,
        require_dual_control=tenant.require_dual_control if tenant else False,
        return_reasons=return_reasons,
        recommended_return_reason_id=recommended_ach_return_reason_id or next(
            (r.id for r in return_reasons if r.reason_text == item.recommended_reason_code), None),
    )


@router.post("/bulk")
def decide_several(
    request: Request,
    exception_ids: list[uuid.UUID] = Form(default_factory=list),
    outcome: str = Form(""),
    check_return_reason_id: str = Form(""),
    ach_return_reason_id: str = Form(""),
    notes: str = Form(""),
    back: str = Form("/ui/exceptions"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:read")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Several exceptions at once (UX_PLAN.md U4, decision U-D2): each is decided, or
    recommended, exactly as it would be from its own page, by the same rules (dual
    control, segregation of duties, return reasons), in its own transaction. Whatever can't
    be done is listed afterwards; nothing is relaxed for doing them together."""
    from openclear.auth.sod import SodViolation
    from openclear.web.security import safe_next_path

    back = safe_next_path(back, default="/ui/exceptions")
    if not back.startswith("/ui/exceptions"):
        back = "/ui/exceptions"
    joiner = "&" if "?" in back else "?"
    choice = _parse_outcome(outcome)
    if choice is None or not exception_ids:
        return RedirectResponse(f"{back}{joiner}error={quote('Select one or more items, then Pay or Return.')}", status_code=303)
    if len(exception_ids) > 200:
        return RedirectResponse(f"{back}{joiner}error={quote('At most 200 items at once.')}", status_code=303)
    dual = db.get(Tenant, ctx.tenant_id).require_dual_control
    can_decide, can_recommend = "exception:decide" in ctx.permissions, "exception:recommend" in ctx.permissions
    done, problems = 0, []
    for exception_id in exception_ids:
        item = exception_service.get_exception(db, ctx.tenant_id, exception_id, customer_id=ctx.customer_id)
        label = f"item {str(exception_id)[:8]}" if item is None else _short_label(db, item)
        if item is None:
            problems.append(f"{label}: not found")
            continue
        recommend_instead = can_recommend and (not can_decide or (dual and item.status == ExceptionStatus.OPEN))
        if not can_decide and not recommend_instead:
            problems.append(f"{label}: you can't decide or recommend items")
            continue
        raw = check_return_reason_id if item.network_code == "check" else ach_return_reason_id
        reason_id, reason_code, problem = _reason_for(db, ctx, exception_id, choice, "", raw)
        if problem:
            problems.append(f"{label}: {problem[0].lower() + problem[1:]}")
            continue
        try:
            if recommend_instead:
                result = decision_service.submit_recommendation(
                    db, ctx.tenant_id, exception_id, ctx, outcome=choice, reason_code=reason_code, notes=notes or None,
                    ach_return_reason_id=reason_id, check_return_reason_id=reason_id)
            else:
                result = decision_service.decide(
                    db, ctx.tenant_id, exception_id, ctx, outcome=choice, reason_code=reason_code, notes=notes or None,
                    ach_return_reason_id=reason_id, check_return_reason_id=reason_id, channel="web")
        except SodViolation as exc:
            problems.append(f"{label}: {exc.violation.message.lower()}")
            continue
        if result.error is not None:
            db.rollback()
            problems.append(f"{label}: {_DECISION_ERROR_MESSAGES[result.error].rstrip('.').lower()}")
            continue
        verb = "Recommended" if recommend_instead else "Decided"
        audit_log_service.record_action(
            db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web",
            action="exception.recommend" if recommend_instead else "exception.decide",
            summary=f"{verb} {choice.value} on exception ({reason_code}), with others at once",
            resource_type="exception_item", resource_id=exception_id,
        )
        db.commit()
        done += 1
    word = "Paid" if choice == DecisionOutcome.PAY else "Returned"
    message = f"{word} {done} {'item' if done == 1 else 'items'}." if done else ""
    if problems:
        shown = "; ".join(problems[:5]) + (f"; and {len(problems) - 5} more" if len(problems) > 5 else "")
        error = f"{len(problems)} couldn't be done: {shown}."
        return RedirectResponse(f"{back}{joiner}{'flash=' + quote(message) + '&' if message else ''}error={quote(error)}", status_code=303)
    return RedirectResponse(f"{back}{joiner}flash={quote(message)}&event=exception-decided", status_code=303)


def _short_label(db: Session, item) -> str:
    source = _load_sources(db, [item]).get(item.id)
    return _summarize_source_item(item.network_code, source)["label"] if source else f"item {str(item.id)[:8]}"


@router.post("/{exception_id}/recommend")
def recommend(
    exception_id: uuid.UUID,
    outcome: str = Form(""),
    reason_code: str = Form(""),
    notes: str = Form(""),
    ach_return_reason_id: str = Form(""),
    return_reason_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:recommend")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    outcome = _parse_outcome(outcome)
    if outcome is None:
        return RedirectResponse(f"/ui/exceptions/{exception_id}?error={quote(_CHOOSE_OUTCOME)}", status_code=303)
    reason_id, reason_code, problem = _reason_for(db, ctx, exception_id, outcome, reason_code, return_reason_id or ach_return_reason_id)
    if problem:
        return RedirectResponse(f"/ui/exceptions/{exception_id}?error={quote(problem)}", status_code=303)
    result = decision_service.submit_recommendation(
        db, ctx.tenant_id, exception_id, ctx, outcome=outcome, reason_code=reason_code, notes=notes or None,
        ach_return_reason_id=reason_id, check_return_reason_id=reason_id,
    )
    if result.error is None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="exception.recommend",
            summary=f"Recommended {outcome.value} on exception ({reason_code})",
            resource_type="exception_item",
            resource_id=exception_id,
        )
    db.commit()
    if result.error is not None:
        message = _DECISION_ERROR_MESSAGES[result.error]
        return RedirectResponse(f"/ui/exceptions/{exception_id}?error={quote(message)}", status_code=303)
    return RedirectResponse(f"/ui/exceptions/{exception_id}?flash=Recommendation+submitted.&event=exception-recommended", status_code=303)


@router.post("/{exception_id}/decide")
def decide(
    exception_id: uuid.UUID,
    outcome: str = Form(""),
    reason_code: str = Form(""),
    notes: str = Form(""),
    ach_return_reason_id: str = Form(""),
    return_reason_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:decide")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    outcome = _parse_outcome(outcome)
    if outcome is None:
        return RedirectResponse(f"/ui/exceptions/{exception_id}?error={quote(_CHOOSE_OUTCOME)}", status_code=303)
    reason_id, reason_code, problem = _reason_for(db, ctx, exception_id, outcome, reason_code, return_reason_id or ach_return_reason_id)
    if problem:
        return RedirectResponse(f"/ui/exceptions/{exception_id}?error={quote(problem)}", status_code=303)
    result = decision_service.decide(
        db, ctx.tenant_id, exception_id, ctx, outcome=outcome, reason_code=reason_code, notes=notes or None,
        ach_return_reason_id=reason_id, check_return_reason_id=reason_id, channel="web",
    )
    if result.error is None:
        audit_log_service.record_action(
            db,
            ctx.tenant_id,
            actor_user_id=ctx.user_id,
            channel="web",
            action="exception.decide",
            summary=f"Decided {outcome.value} on exception ({reason_code})",
            resource_type="exception_item",
            resource_id=exception_id,
        )
    db.commit()
    if result.error is not None:
        message = _DECISION_ERROR_MESSAGES[result.error]
        return RedirectResponse(f"/ui/exceptions/{exception_id}?error={quote(message)}", status_code=303)
    # `event` tells an embedding online banking page (static/js/app.js); ignored otherwise.
    return RedirectResponse(f"/ui/exceptions/{exception_id}?flash=Decision+recorded.&event=exception-decided", status_code=303)
