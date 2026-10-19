# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Search everywhere (UX_PLAN.md U12): one box in the header finds checks, ACH entries and
exceptions by check or trace number, payee or originator, or amount, among what the person
may see. Each section links on to its full list with the same search."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.repositories.ach_transaction_repo import AchTransactionRepository
from openclear.repositories.issued_item_repo import IssuedItemRepository
from openclear.repositories.paid_item_repo import PaidItemRepository
from openclear.services import exception_service
from openclear.web import list_search
from openclear.web.deps import get_web_context, render_template
from openclear.web.security import verify_csrf
from openclear.web.redirects import see_other

router = APIRouter(tags=["web-search"])
PER_SECTION = 10


@router.post("/ui/list-search")
async def list_search_post(request: Request, db: Session = Depends(get_db), ctx: TenantContext = Depends(get_web_context),
                           _csrf: None = Depends(verify_csrf)) -> RedirectResponse:
    """A list's filters, with an account number turned into account ids (ASVS 5.0 14.2.1)."""
    return see_other(list_search.resolve_and_redirect(await request.form(), db, ctx))


@router.get("/ui/search")
def search_page(request: Request, q: str = "", db: Session = Depends(get_db),
                ctx: TenantContext = Depends(get_web_context)) -> HTMLResponse:
    """Old links with ?q= still work; the header's box posts, so new searches stay out of URLs."""
    return _search(request, q, db, ctx)


@router.post("/ui/search")
def search_everywhere(request: Request, q: str = Form(""), db: Session = Depends(get_db),
                      ctx: TenantContext = Depends(get_web_context), _csrf: None = Depends(verify_csrf)) -> HTMLResponse:
    """Posted (OWASP_PLAN.md OW-D35): what's typed, perhaps an account number, never reaches a URL."""
    return _search(request, q, db, ctx)


def _search(request: Request, q: str, db: Session, ctx: TenantContext) -> HTMLResponse:
    q = q.strip()[:100]
    search = list_search.Search({"q": q})
    sections = []
    if q:
        def section(title, permission, list_path, spec, repo_list, repo_count, link):
            if permission not in ctx.permissions:
                return
            where = spec.where(search)
            total = repo_count(where)
            if total:
                sections.append({"title": title, "total": total, "more": f"{list_path}?q={quote(q)}",
                                 "rows": [link(row) for row in repo_list(where)]})

        issued = IssuedItemRepository(db, ctx.tenant_id, ctx.customer_id)
        paid = PaidItemRepository(db, ctx.tenant_id, ctx.customer_id)
        ach = AchTransactionRepository(db, ctx.tenant_id, ctx.customer_id)
        exception_spec = list_search.exceptions_spec()
        section("Exceptions", "exception:read", "/ui/exceptions?status=all", exception_spec,
                lambda w: exception_service.list_exceptions(db, ctx.tenant_id, customer_id=ctx.customer_id, where=w,
                                                            order_by=exception_spec.default_order(), limit=PER_SECTION),
                lambda w: exception_service.count_exceptions(db, ctx.tenant_id, customer_id=ctx.customer_id, where=w),
                lambda e: {"href": f"/ui/exceptions/{e.id}", "label": f"{e.network_code.upper()} exception",
                           "detail": f"{e.status.value.replace('_', ' ')} · {e.exception_types.replace(',', ', ')}"})
        section("Issued checks", "issued_item:read", "/ui/issued-items", list_search.issued_items_spec(),
                lambda w: issued.list(where=w, order_by=[issued.model.created_at.desc()], limit=PER_SECTION),
                lambda w: issued.count(where=w),
                lambda r: {"href": f"/ui/issued-items/{r.id}", "label": f"Check {r.check_number}", "amount": r.amount,
                           "detail": f"{r.payee_name} · issued {r.issue_date} · {r.status.value}"})
        section("Presented checks", "paid_item:read", "/ui/paid-items", list_search.paid_items_spec(),
                lambda w: paid.list(where=w, order_by=[paid.model.created_at.desc()], limit=PER_SECTION),
                lambda w: paid.count(where=w),
                lambda r: {"href": f"/ui/paid-items/{r.id}", "label": f"Check {r.check_number}", "amount": r.presented_amount,
                           "detail": f"presented {r.presented_date} · {r.match_status.value}"})
        section("ACH transactions", "ach_transaction:read", "/ui/ach/transactions", list_search.ach_transactions_spec(),
                lambda w: ach.list(where=w, order_by=[ach.model.created_at.desc()], limit=PER_SECTION),
                lambda w: ach.count(where=w),
                lambda r: {"href": f"/ui/ach/transactions/{r.id}", "label": f"{r.originator_name}", "amount": r.amount,
                           "detail": f"trace {r.trace_number} · {r.effective_date} · {r.match_status.value}"})
    return render_template(request, "search.html", ctx=ctx, q=q, sections=sections)
