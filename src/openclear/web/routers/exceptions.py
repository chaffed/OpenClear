# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
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
from openclear.web.deps import WebForbidden, WebNotFound, render_template, require_web_permission
from openclear.web.pagination import paginate
from openclear.web.redirects import see_other
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
    DecisionError.NOT_PENDING_APPROVAL: "There's no recommendation awaiting approval to withdraw; it may have been decided already.",
    DecisionError.NOT_YOUR_RECOMMENDATION: "Only the person who made a recommendation can withdraw it.",
    DecisionError.NOT_DECIDED: "There's no decision to change; it may have been taken back.",
    DecisionError.LOCKED: "The cut-off has passed: this decision is final.",
    DecisionError.CHANGE_NEEDS_APPROVAL: "Under dual control, recommend the change; someone else approves it.",
}

# A pseudo-status for the list: decided, but still changeable before the cut-off (UI_PLAN.md UI-D13).
_CHANGEABLE = "changeable"


_CHOOSE_OUTCOME = "Choose Pay or Return."


def _merged(filters: dict, where) -> dict:
    """The list's own filters, with a search's conditions added to any of its own."""
    merged = dict(filters)
    merged["where"] = [*(filters.get("where") or ()), *(where or ())] or None
    return merged


def _undo_last(request: Request, db: Session, ctx: TenantContext) -> dict | None:
    """The toast's Undo after moving on to the next item (UI_PLAN.md UI-D11, UI-D13): the form
    to take back what this person just did on the item before, if it can still be taken back.
    Checked here, not trusted from the link: only their own decision before its cut-off (and not
    under dual control), or their own recommendation still waiting."""
    kind, raw = request.query_params.get("undo_kind"), request.query_params.get("undo_item")
    if request.query_params.get("undo") != "undo-last" or kind not in ("decision", "recommendation") or not raw:
        return None
    try:
        item_id = uuid.UUID(raw)
    except ValueError:
        return None
    item = exception_service.get_exception(db, ctx.tenant_id, item_id, customer_id=ctx.customer_id)
    if item is None:
        return None
    if kind == "recommendation":
        ok = item.status == ExceptionStatus.PENDING_APPROVAL and item.recommended_by_user_id == ctx.user_id
        return {"action": f"/ui/exceptions/{item.id}/recommendation/withdraw"} if ok else None
    decision = decision_service.get_decision_for_exception(db, ctx.tenant_id, item.id, customer_id=ctx.customer_id)
    tenant = db.get(Tenant, ctx.tenant_id)
    ok = (decision is not None and decision.locked_at is None and decision.decided_by_user_id == ctx.user_id
          and decision_service.changeable_until(item) is not None and not tenant.require_dual_control)
    return {"action": f"/ui/exceptions/{item.id}/decision/undo"} if ok else None


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
    customer: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:read")),
):
    """Defaults to what still needs a person (open, or awaiting approval), oldest first:
    a work queue. Choosing a decided status, or "All", shows newest first instead."""
    changeable_only = status == _CHANGEABLE
    if status == _ALL_STATUSES:
        statuses, oldest_first = None, False
    elif changeable_only:
        statuses, oldest_first = (ExceptionStatus.PAY, ExceptionStatus.RETURN), True
    elif status in {s.value for s in ExceptionStatus}:
        statuses = (ExceptionStatus(status),)
        oldest_first = statuses[0] in exception_service.NEEDS_ATTENTION
    else:
        status, statuses, oldest_first = "", exception_service.NEEDS_ATTENTION, True

    from openclear.web import list_search

    # Bank staff can narrow to one customer (the customer hub links here); a customer's own
    # people only ever see their own.
    customer_id = ctx.customer_id
    if customer_id is None and customer:
        try:
            customer_id = uuid.UUID(customer)
        except ValueError:
            customer_id = None
    filters = dict(network_code=network_code or None, statuses=statuses, customer_id=customer_id)
    if changeable_only:
        from openclear.domain.decision import Decision

        unlocked = select(Decision.exception_item_id).where(Decision.tenant_id == ctx.tenant_id, Decision.locked_at.is_(None))
        filters["where"] = [ExceptionItem.id.in_(unlocked)]
    search = list_search.parse(request, extra=("status", "network_code", "customer"))
    spec = list_search.exceptions_spec()
    # A work queue (open or awaiting approval) is oldest first, unless another sort is chosen.
    spec.default_order = lambda: [ExceptionItem.created_at.asc() if oldest_first else ExceptionItem.created_at.desc()]
    page_obj = list_search.page_or_export(
        request=request, db=db, ctx=ctx, search=search, spec=spec, page=page, list_name="exceptions",
        count=lambda where: exception_service.count_exceptions(db, ctx.tenant_id, **_merged(filters, where)),
        fetch=lambda **kw: exception_service.list_exceptions(db, ctx.tenant_id, **_merged(filters, kw.pop("where", None)), **kw),
    )
    if not hasattr(page_obj, "items"):
        return page_obj
    # Checks to review (reverse positive pay), across every page of the work queue (RPP-D6).
    to_review = 0
    if statuses == exception_service.NEEDS_ATTENTION and not search.active:
        to_review = exception_service.count_exceptions(
            db, ctx.tenant_id, statuses=statuses, customer_id=customer_id, where=_review_only_where(network_code or None))
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
        undo_last=_undo_last(request, db, ctx),
        status_filter=status, statuses=[s.value for s in ExceptionStatus], search=search,
        can_act=bool({"exception:decide", "exception:recommend"} & set(ctx.permissions)),
        bulk_check_reasons=check_return_reason_service.list_check_return_reasons(db, ctx.tenant_id),
        bulk_ach_reasons=ach_return_reason_service.list_ach_return_reasons(db, ctx.tenant_id, active_only=True),
        show_scores=any(r["exception"].ml_score is not None for r in rows),
        show_deadlines=any(r["exception"].decision_deadline is not None for r in rows),
        show_customers=ctx.customer_id is None and any(r["customer"] for r in rows),
        customer_filter=str(customer_id) if customer_id and ctx.customer_id is None else "",
        customer_choices=_customer_choices(db, ctx), to_review=to_review, max_pay_all=MAX_PAY_ALL,
    )


def _customer_choices(db: Session, ctx: TenantContext) -> list[tuple[str, str]]:
    """For bank staff's Customer filter (FULL_REVIEW_PLAN.md FR-D14): every active customer, by
    name. A customer's own people have no choice to make."""
    if ctx.customer_id is not None:
        return []
    return [(str(c_id), name) for c_id, name in db.execute(
        select(Customer.id, Customer.name).where(Customer.tenant_id == ctx.tenant_id, Customer.is_active.is_(True))
        .order_by(Customer.name)).all()]


@router.get("/approvals")
def list_approvals(
    request: Request,
    network_code: str | None = None,
    customer: str = "",
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
    # Recommendations waiting, and changes to decisions waiting before their cut-off (UI_PLAN.md UI-D13).
    waiting = [or_(ExceptionItem.status == ExceptionStatus.PENDING_APPROVAL, ExceptionItem.change_recommended_at.is_not(None))]
    customer_id = ctx.customer_id
    if customer_id is None and customer:
        try:
            customer_id = uuid.UUID(customer)
        except ValueError:
            customer_id = None
    page_obj = paginate(
        page=page,
        count_fn=lambda: exception_service.count_exceptions(db, ctx.tenant_id, network_code=network_code, customer_id=customer_id,
                                                            where=waiting),
        list_fn=lambda **kw: exception_service.list_exceptions(
            db, ctx.tenant_id, network_code=network_code, where=waiting,
            customer_id=customer_id,
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
    _accounts, customers = _names_for_page(db, ctx, page_obj.items, sources)
    rows = []
    for item in page_obj.items:
        source_item = sources[item.id]
        rows.append(
            {
                "exception": item,
                "summary": _summarize_source_item(item.network_code, source_item) if source_item else {"label": "(missing)", "amount": None, "date": ""},
                "recommended_by_email": recommender_emails.get(item.recommended_by_user_id),
                "is_own_recommendation": item.recommended_by_user_id == ctx.user_id,
                "customer": customers.get(item.customer_id, ""),
            }
        )
    return render_template(
        request, "exceptions/approvals.html", ctx=ctx, rows=rows, page_obj=page_obj, network_filter=network_code,
        customer_filter=str(customer_id) if customer_id and ctx.customer_id is None else "", customer_choices=_customer_choices(db, ctx),
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
    # The item's own action log (MOBILE_PLAN.md R2), for those who may read the action log;
    # never in a customer's session (it names the bank's people).
    history = _history(db, ctx.tenant_id, item.id) if "audit_log:read" in ctx.permissions and ctx.customer_id is None else []
    # Changeable until the cut-off (UI_PLAN.md UI-D13), and every earlier version.
    changeable_until = decision_service.changeable_until(item) if decision is not None and decision.locked_at is None else None
    revisions = decision_service.revisions(db, ctx.tenant_id, item.id)
    people = {u.id: u.email for u in db.execute(select(User).where(User.id.in_(
        {r.decided_by_user_id for r in revisions} | {r.revised_by_user_id for r in revisions} - {None}))).scalars()} if revisions else {}

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
        history=history,
        changeable_until=changeable_until,
        revisions=revisions,
        people=people,
        undo_last=_undo_last(request, db, ctx),
    )


_HISTORY_LIMIT = 50


def _history(db: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID) -> list[dict[str, Any]]:
    """The exception's entries in the action log, newest first, with who did each."""
    from openclear.domain.audit_log_entry import AuditLogEntry

    rows = db.execute(
        select(AuditLogEntry, User.email).outerjoin(User, User.id == AuditLogEntry.actor_user_id)
        .where(AuditLogEntry.tenant_id == tenant_id, AuditLogEntry.resource_id == exception_id,
               AuditLogEntry.resource_type == "exception_item")
        .order_by(AuditLogEntry.sequence.desc()).limit(_HISTORY_LIMIT)
    ).all()
    return [{"entry": entry, "actor_email": email or "(system)"} for entry, email in rows]


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
    from openclear.web.security import safe_next_path

    back = safe_next_path(back, default="/ui/exceptions")
    if not back.startswith("/ui/exceptions"):
        back = "/ui/exceptions"
    choice = _parse_outcome(outcome)
    if choice is None or not exception_ids:
        return see_other(back, error="Select one or more items, then Pay or Return.")
    if len(exception_ids) > 200:
        return see_other(back, error="At most 200 items at once.")
    done, problems = _decide_each(db, ctx, exception_ids, choice, check_return_reason_id=check_return_reason_id,
                                  ach_return_reason_id=ach_return_reason_id, notes=notes, together="with others at once")
    word = "Paid" if choice == DecisionOutcome.PAY else "Returned"
    message = f"{word} {done} {'item' if done == 1 else 'items'}." if done else ""
    if problems:
        shown = "; ".join(problems[:5]) + (f"; and {len(problems) - 5} more" if len(problems) > 5 else "")
        error = f"{len(problems)} couldn't be done: {shown}."
        return see_other(back, flash=message or None, error=error)
    return see_other(back, flash=message, event="exception-decided")


def _decide_each(db: Session, ctx: TenantContext, exception_ids: list[uuid.UUID], choice: DecisionOutcome, *, check_return_reason_id: str,
                 ach_return_reason_id: str, notes: str, together: str) -> tuple[int, list[str]]:
    """Each exception decided, or recommended, exactly as from its own page, in its own
    transaction: (how many were done, what couldn't be and why)."""
    from openclear.auth.sod import SodViolation

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
            summary=f"{verb} {choice.value} on exception ({reason_code}), {together}",
            resource_type="exception_item", resource_id=exception_id,
        )
        db.commit()
        done += 1
    return done, problems


# --- Reverse positive pay: the day's checks in one pass (REVERSE_POSITIVE_PAY_PLAN.md RPP-D6) -----

MAX_PAY_ALL = 500


def _review_only_where(network_code: str | None) -> list:
    """Checks to review with no other problem: only reverse positive pay accounts have them."""
    if network_code not in (None, "", "check"):
        return [ExceptionItem.id.is_(None)]
    return [ExceptionItem.network_code == "check", ExceptionItem.exception_types == "review"]


def _review_customer(ctx: TenantContext, customer: str) -> uuid.UUID | None:
    if ctx.customer_id is not None or not customer:
        return ctx.customer_id
    try:
        return uuid.UUID(customer)
    except ValueError:
        return None


@router.post("/review/pay-all")
def pay_all_to_review(
    customer: str = Form(""),
    network_code: str = Form(""),
    expected: int = Form(...),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:read")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Pays every check to review on the list as shown, after the person confirmed how many.
    If the list changed since (one decided, a new one arrived), nothing is done and they look
    again. Each is still its own decision, by the same rules as one at a time."""
    back = "/ui/exceptions" + (f"?customer={customer}" if customer and ctx.customer_id is None else "")
    filters = dict(statuses=exception_service.NEEDS_ATTENTION, customer_id=_review_customer(ctx, customer),
                   where=_review_only_where(network_code or None))
    items = exception_service.list_exceptions(db, ctx.tenant_id, limit=MAX_PAY_ALL + 1, **filters)
    if len(items) > MAX_PAY_ALL:
        return see_other(back, error=f"There are more than {MAX_PAY_ALL} checks to review; narrow the list to one customer first.")
    if len(items) != expected:
        return see_other(back, error="The list changed since you looked: check it again, then pay.")
    done, problems = _decide_each(db, ctx, [item.id for item in items], DecisionOutcome.PAY, check_return_reason_id="",
                                  ach_return_reason_id="", notes="", together="paying every check to review")
    message = f"Paid {done} {'check' if done == 1 else 'checks'}." if done else ""
    if problems:
        shown = "; ".join(problems[:5]) + (f"; and {len(problems) - 5} more" if len(problems) > 5 else "")
        return see_other(back, flash=message or None, error=f"{len(problems)} couldn't be done: {shown}.")
    return see_other(back, flash=message, event="exception-decided")


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
    from_list: str = Form("", alias="from"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:recommend")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    outcome = _parse_outcome(outcome)
    if outcome is None:
        return see_other(f"/ui/exceptions/{exception_id}", error=_CHOOSE_OUTCOME)
    reason_id, reason_code, problem = _reason_for(db, ctx, exception_id, outcome, reason_code, return_reason_id or ach_return_reason_id)
    if problem:
        return see_other(f"/ui/exceptions/{exception_id}", error=problem)
    # A fresh recommendation can be undone from the toast that confirms it; a revised one
    # can't (undoing it would withdraw the earlier one too).
    before = exception_service.get_exception(db, ctx.tenant_id, exception_id, customer_id=ctx.customer_id)
    fresh = before is not None and before.status == ExceptionStatus.OPEN
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
        return see_other(f"/ui/exceptions/{exception_id}", error=message)
    # On to the next item, as after a decision (UI_PLAN.md UI-D11); a fresh recommendation can be
    # withdrawn from the toast there.
    undo = {"undo": "undo-last", "undo_kind": "recommendation", "undo_item": str(exception_id)} if fresh else {}
    queue = _queue_path(from_list)
    following = _next_to_decide(db, ctx, exception_id, queue) if queue else None
    if following is None:
        return see_other(f"/ui/exceptions/{exception_id}", flash="Recommendation submitted.", event="exception-recommended",
                         undo="withdraw-recommendation" if fresh else None)
    return see_other(f"/ui/exceptions/{following.id}", flash="Recommendation submitted.", event="exception-recommended",
                     **{"from": queue}, **undo)


@router.post("/{exception_id}/recommendation/withdraw")
def withdraw_recommendation(
    exception_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:recommend")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Undo (MOBILE_PLAN.md R2): one's own recommendation, before it's approved."""
    result = decision_service.withdraw_recommendation(db, ctx.tenant_id, exception_id, ctx)
    if result.error is not None:
        db.rollback()
        return see_other(f"/ui/exceptions/{exception_id}", error=_DECISION_ERROR_MESSAGES[result.error])
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="exception.recommendation_withdrawn",
        summary="Withdrew their recommendation; the exception is open again",
        resource_type="exception_item", resource_id=exception_id,
    )
    db.commit()
    return see_other(f"/ui/exceptions/{exception_id}", flash="Recommendation withdrawn.")


@router.post("/{exception_id}/decide")
def decide(
    exception_id: uuid.UUID,
    outcome: str = Form(""),
    reason_code: str = Form(""),
    notes: str = Form(""),
    ach_return_reason_id: str = Form(""),
    return_reason_id: str = Form(""),
    from_list: str = Form("", alias="from"),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:decide")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    outcome = _parse_outcome(outcome)
    if outcome is None:
        return see_other(f"/ui/exceptions/{exception_id}", error=_CHOOSE_OUTCOME)
    reason_id, reason_code, problem = _reason_for(db, ctx, exception_id, outcome, reason_code, return_reason_id or ach_return_reason_id)
    if problem:
        return see_other(f"/ui/exceptions/{exception_id}", error=problem)
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
        return see_other(f"/ui/exceptions/{exception_id}", error=message)
    # On to the next item still to decide, in the queue it was opened from (UI_PLAN.md UI-D11).
    # `event` tells an embedding online banking page (static/js/app.js); ignored otherwise.
    label = _short_label(db, result.exception)
    done = f"{label[:1].upper()}{label[1:]}: {'paid' if outcome == DecisionOutcome.PAY else 'returned'}."
    queue = _queue_path(from_list)
    # Undo in the toast while it can still change (UI-D13); the page it lands on checks it.
    undo = {"undo": "undo-last", "undo_kind": "decision", "undo_item": str(exception_id)} if result.decision.locked_at is None else {}
    if queue is None:
        return see_other(f"/ui/exceptions/{exception_id}", flash=done, event="exception-decided", **undo)
    following = _next_to_decide(db, ctx, exception_id, queue)
    if following is None:
        return see_other(queue, flash=f"{done} Nothing else is waiting for you here.", event="exception-decided", **undo)
    return see_other(f"/ui/exceptions/{following.id}", flash=done, event="exception-decided", **{"from": queue}, **undo)


@router.post("/{exception_id}/decision/change")
def change_decision(
    exception_id: uuid.UUID,
    outcome: str = Form(""),
    reason_code: str = Form(""),
    notes: str = Form(""),
    ach_return_reason_id: str = Form(""),
    return_reason_id: str = Form(""),
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:read")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Change a decision before its cut-off (UI_PLAN.md UI-D13). Under dual control: recommend
    the change, or approve one someone else recommended."""
    back = f"/ui/exceptions/{exception_id}"
    parsed = _parse_outcome(outcome)
    if parsed is None:
        return see_other(back, error=_CHOOSE_OUTCOME)
    item = exception_service.get_exception(db, ctx.tenant_id, exception_id, customer_id=ctx.customer_id)
    if item is None:
        raise WebNotFound()
    proposing = db.get(Tenant, ctx.tenant_id).require_dual_control and item.change_recommended_at is None
    if ("exception:recommend" if proposing else "exception:decide") not in ctx.permissions:
        raise WebForbidden()
    reason_id, reason_code, problem = _reason_for(db, ctx, exception_id, parsed, reason_code, return_reason_id or ach_return_reason_id)
    if problem:
        return see_other(back, error=problem)
    if proposing:
        result = decision_service.recommend_change(db, ctx.tenant_id, exception_id, ctx, outcome=parsed, reason_code=reason_code,
                                                   notes=notes or None, ach_return_reason_id=reason_id, check_return_reason_id=reason_id)
    else:
        result = decision_service.change_decision(db, ctx.tenant_id, exception_id, ctx, outcome=parsed, reason_code=reason_code,
                                                  notes=notes or None, ach_return_reason_id=reason_id, check_return_reason_id=reason_id,
                                                  channel="web")
    if result.error is not None:
        db.rollback()
        return see_other(back, error=_DECISION_ERROR_MESSAGES[result.error])
    verb = "Recommended changing" if proposing else "Changed"
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web",
        action="exception.recommend_change" if proposing else "exception.change_decision",
        summary=f"{verb} the decision to {parsed.value} before the cut-off ({reason_code})",
        resource_type="exception_item", resource_id=exception_id,
    )
    db.commit()
    word = "paying" if parsed == DecisionOutcome.PAY else "returning"
    return see_other(back, flash=f"Recommended {word} instead: someone else approves the change." if proposing
                     else f"Changed to {parsed.value}.")


@router.post("/{exception_id}/decision/undo")
def undo_decision(
    exception_id: uuid.UUID,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(require_web_permission("exception:decide")),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    """Take a decision back before its cut-off: the item is open to decide again."""
    back = f"/ui/exceptions/{exception_id}"
    result = decision_service.undo_decision(db, ctx.tenant_id, exception_id, ctx)
    if result.error is not None:
        db.rollback()
        return see_other(back, error=_DECISION_ERROR_MESSAGES[result.error])
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="exception.undo_decision",
        summary="Took the decision back before the cut-off", resource_type="exception_item", resource_id=exception_id,
    )
    db.commit()
    return see_other(back, flash="Decision taken back: the item is open to decide again.")


def _queue_path(from_list: str) -> str | None:
    """The exceptions list an item was opened from, if it's a work queue (what still needs a
    person); None for a list of decided items, or anything that isn't the exceptions list."""
    from openclear.web.security import safe_next_path

    path = safe_next_path(from_list, default="")
    if not (path == "/ui/exceptions" or path.startswith("/ui/exceptions?")):
        return None
    status = parse_qs(urlsplit(path).query).get("status", [""])[0]
    return path if status in ("", *(s.value for s in exception_service.NEEDS_ATTENTION)) else None


def _next_to_decide(db: Session, ctx: TenantContext, current_id: uuid.UUID, queue: str) -> ExceptionItem | None:
    """The oldest other item in that queue (its network and customer filters) this person can
    still act on: not one waiting on their own recommendation."""
    params = parse_qs(urlsplit(queue).query)
    status = params.get("status", [""])[0]
    wanted = {ExceptionStatus(status)} if status else set(exception_service.NEEDS_ATTENTION)
    # What this person can act on: under dual control the maker recommends open items and the
    # checker approves recommended ones; otherwise a decider decides either.
    can_decide, can_recommend = "exception:decide" in ctx.permissions, "exception:recommend" in ctx.permissions
    if db.get(Tenant, ctx.tenant_id).require_dual_control:
        actable = ({ExceptionStatus.PENDING_APPROVAL} if can_decide else set()) | ({ExceptionStatus.OPEN} if can_recommend else set())
    else:
        actable = set(exception_service.NEEDS_ATTENTION) if can_decide else {ExceptionStatus.OPEN}
    statuses = tuple(wanted & actable)
    if not statuses:
        return None
    customer_id = ctx.customer_id
    if customer_id is None and params.get("customer"):
        try:
            customer_id = uuid.UUID(params["customer"][0])
        except ValueError:
            customer_id = None
    found = exception_service.list_exceptions(
        db, ctx.tenant_id, network_code=params.get("network_code", [""])[0] or None, statuses=statuses, customer_id=customer_id,
        where=[ExceptionItem.id != current_id,
               or_(ExceptionItem.recommended_by_user_id.is_(None), ExceptionItem.recommended_by_user_id != ctx.user_id)],
        order_by=ExceptionItem.created_at.asc(), limit=1,
    )
    return found[0] if found else None
