# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Searching, filtering, sorting and exporting the main lists on the server, across every
row, not just the page on screen (UX_PLAN.md U12; MASTER_PLAN.md M2).

Each list (issued items, paid items, ACH transactions, exceptions) has a `Spec`: which
filters it offers, how each becomes a SQL condition, and which columns it sorts by. The
same parsed `Search` drives the page, its pager links and its CSV export, so what's
exported is exactly what's listed. Everything runs inside the list's own repository, so a
search never reaches another bank's or another customer's rows."""

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import or_, select
from starlette.requests import Request

# Accounts are filtered by id, never by number: an account number in a URL reaches access logs,
# browser history and Referer headers (ASVS 5.0 14.2.1; OWASP_PLAN.md OW-D35). The form posts the
# number typed to /ui/list-search, which turns it into ids (resolve_and_redirect).
FIELDS = ("q", "number", "amount_min", "amount_max", "date_from", "date_to", "account_id", "sort", "dir")
LISTS = ("/ui/issued-items", "/ui/paid-items", "/ui/ach/transactions", "/ui/exceptions")
MAX_ACCOUNTS = 20
EXPORT_LIMIT = 100_000
_MONEY = re.compile(r"^\$?\s*([0-9][0-9,]*)(\.[0-9]{1,2})?$")


def parse_amount(text: str | None) -> Decimal | None:
    match = _MONEY.match((text or "").strip())
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", "") + (match.group(2) or ""))
    except InvalidOperation:
        return None


def _date(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "").strip()) if text else None
    except ValueError:
        return None


@dataclass
class Search:
    """What the person asked for, as typed (for refilling the form) and as parsed."""

    values: dict[str, str] = field(default_factory=dict)
    # The account numbers behind account_id, to refill the form (page_or_export); never in a URL.
    account_label: str = ""

    @property
    def account_ids(self) -> list:
        import uuid

        out = []
        for part in self.get("account_id").split(","):
            try:
                out.append(uuid.UUID(part.strip()))
            except ValueError:
                continue
        return out[:MAX_ACCOUNTS]

    def get(self, name: str) -> str:
        return self.values.get(name, "")

    @property
    def active(self) -> bool:
        return any(self.values.get(name) for name in FIELDS if name not in ("sort", "dir"))

    def query_string(self, **overrides: Any) -> str:
        """These filters as a query string, with overrides (a page number, a sort, an export)."""
        merged = {k: v for k, v in self.values.items() if v}
        for key, value in overrides.items():
            if value in (None, ""):
                merged.pop(key, None)
            else:
                merged[key] = str(value)
        return urlencode(merged)

    def sort_link(self, column: str) -> str:
        """The query string that sorts by `column`, flipping the direction if it's already sorted by it."""
        flip = self.get("sort") == column and self.get("dir") != "desc"
        return self.query_string(sort=column, dir="desc" if flip else "asc", page=None)

    def sort_state(self, column: str) -> str | None:
        if self.get("sort") != column:
            return None
        return "descending" if self.get("dir") == "desc" else "ascending"


def parse(request: Request, extra: tuple[str, ...] = ()) -> Search:
    values = {name: request.query_params.get(name, "").strip()[:100] for name in FIELDS + extra if name != "account_id"}
    values["account_id"] = ",".join(v.strip() for v in request.query_params.getlist("account_id") if v.strip())[:40 * MAX_ACCOUNTS]
    return Search(values)


@dataclass
class Spec:
    """One list's filters and sort columns. `conditions` turns a Search into SQL
    conditions; `sorts` maps a sort name to a column; `default_sort` is (column, descending)."""

    conditions: Callable[[Search], list]
    sorts: dict[str, Any]
    default_order: Callable[[], Any]
    export_columns: list[tuple[str, Callable[[Any], Any]]]
    # Last in every order, so rows that tie (made in the same second, the same amount) keep
    # one order from page to page.
    tie_breaker: Any = None

    def where(self, search: Search) -> list:
        return self.conditions(search)

    def order_by(self, search: Search):
        column = self.sorts.get(search.get("sort"))
        order = self.default_order() if column is None else [column.desc() if search.get("dir") == "desc" else column.asc()]
        return [*order, self.tie_breaker] if self.tie_breaker is not None else order


def _accounts(column, s: Search) -> list:
    """account_id given: only those accounts (none at all if none of them is a real id)."""
    if not s.get("account_id"):
        return []
    return [column.in_(s.account_ids)]


def _range(column, search: Search, low: str, high: str, parse_fn) -> list:
    conditions = []
    if (value := parse_fn(search.get(low))) is not None:
        conditions.append(column >= value)
    if (value := parse_fn(search.get(high))) is not None:
        conditions.append(column <= value)
    return conditions


# --- The lists --------------------------------------------------------------------------------

def issued_items_spec() -> Spec:
    from openclear.domain.issued_item import IssuedItem as I

    def conditions(s: Search) -> list:
        out = _range(I.amount, s, "amount_min", "amount_max", parse_amount) + _range(I.issue_date, s, "date_from", "date_to", _date)
        if s.get("number"):
            out.append(I.check_number == s.get("number"))
        out += _accounts(I.account_id, s)
        if q := s.get("q"):
            amount = parse_amount(q)
            out.append(or_(I.check_number == q, I.payee_name.ilike(f"%{q}%"), *([I.amount == amount] if amount is not None else [])))
        return out

    return Spec(conditions, {"number": I.check_number, "amount": I.amount, "payee": I.payee_name, "date": I.issue_date,
                             "status": I.status},
                lambda: [I.created_at.desc()],
                [("check_number", lambda r: r.check_number), ("amount", lambda r: r.amount), ("payee", lambda r: r.payee_name),
                 ("issue_date", lambda r: r.issue_date), ("status", lambda r: r.status.value), ("account_id", lambda r: r.account_id)],
                I.id)


def paid_items_spec() -> Spec:
    from openclear.domain.paid_item import PaidItem as P

    def conditions(s: Search) -> list:
        out = (_range(P.presented_amount, s, "amount_min", "amount_max", parse_amount)
               + _range(P.presented_date, s, "date_from", "date_to", _date))
        if s.get("number"):
            out.append(P.check_number == s.get("number"))
        out += _accounts(P.account_id, s)
        if q := s.get("q"):
            amount = parse_amount(q)
            out.append(or_(P.check_number == q, *([P.presented_amount == amount] if amount is not None else [])))
        return out

    return Spec(conditions, {"number": P.check_number, "amount": P.presented_amount, "date": P.presented_date,
                             "match": P.match_status, "settlement": P.settlement_status},
                lambda: [P.created_at.desc()],
                [("check_number", lambda r: r.check_number), ("amount", lambda r: r.presented_amount),
                 ("presented_date", lambda r: r.presented_date), ("match_status", lambda r: r.match_status.value),
                 ("settlement_status", lambda r: r.settlement_status.value), ("account_id", lambda r: r.account_id)],
                P.id)


def ach_transactions_spec() -> Spec:
    from openclear.domain.ach_transaction import AchTransaction as T

    def conditions(s: Search) -> list:
        out = _range(T.amount, s, "amount_min", "amount_max", parse_amount) + _range(T.effective_date, s, "date_from", "date_to", _date)
        if s.get("number"):
            out.append(T.trace_number == s.get("number"))
        out += _accounts(T.account_id, s)
        if q := s.get("q"):
            amount = parse_amount(q)
            out.append(or_(T.trace_number == q, T.originator_id == q, T.originator_name.ilike(f"%{q}%"),
                           *([T.amount == amount] if amount is not None else [])))
        return out

    return Spec(conditions, {"number": T.trace_number, "amount": T.amount, "originator": T.originator_name, "date": T.effective_date,
                             "match": T.match_status},
                lambda: [T.created_at.desc()],
                [("trace_number", lambda r: r.trace_number), ("amount", lambda r: r.amount), ("type", lambda r: r.transaction_type.value),
                 ("originator_id", lambda r: r.originator_id), ("originator_name", lambda r: r.originator_name),
                 ("sec_code", lambda r: r.sec_code), ("effective_date", lambda r: r.effective_date),
                 ("match_status", lambda r: r.match_status.value), ("account_id", lambda r: r.account_id)],
                T.id)


def exceptions_spec() -> Spec:
    """Exceptions are searched through the item they're about: the check (number, amount,
    date presented, account) or the ACH entry (trace number, originator, amount)."""
    from openclear.domain.ach_transaction import AchTransaction as T
    from openclear.domain.exception_item import ExceptionItem as E
    from openclear.domain.paid_item import PaidItem as P

    def conditions(s: Search) -> list:
        checks, ach = [], []
        if s.get("number"):
            checks.append(P.check_number == s.get("number"))
            ach.append(T.trace_number == s.get("number"))
        for low, high, parse_fn, check_col, ach_col in (("amount_min", "amount_max", parse_amount, P.presented_amount, T.amount),
                                                        ("date_from", "date_to", _date, P.presented_date, T.effective_date)):
            checks += _range(check_col, s, low, high, parse_fn)
            ach += _range(ach_col, s, low, high, parse_fn)
        checks += _accounts(P.account_id, s)
        ach += _accounts(T.account_id, s)
        if q := s.get("q"):
            amount = parse_amount(q)
            checks.append(or_(P.check_number == q, *([P.presented_amount == amount] if amount is not None else [])))
            ach.append(or_(T.trace_number == q, T.originator_id == q, T.originator_name.ilike(f"%{q}%"),
                           *([T.amount == amount] if amount is not None else [])))
        if not checks:
            return []
        return [or_(E.source_item_id.in_(select(P.id).where(*checks)), E.source_item_id.in_(select(T.id).where(*ach)))]

    return Spec(conditions, {"created": E.created_at, "deadline": E.decision_deadline, "status": E.status, "network": E.network_code,
                             "score": E.ml_score},
                lambda: [E.created_at.desc()],
                [("exception_id", lambda r: r.id), ("network", lambda r: r.network_code), ("types", lambda r: r.exception_types),
                 ("status", lambda r: r.status.value), ("created_at", lambda r: r.created_at), ("decide_by", lambda r: r.decision_deadline),
                 ("ml_score", lambda r: r.ml_score)],
                E.id)


# --- Export -----------------------------------------------------------------------------------

def _cell(value: Any) -> Any:
    """A spreadsheet must never run a cell as a formula (CSV injection)."""
    if value is None:
        return ""
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(rows, columns: list[tuple[str, Callable[[Any], Any]]], extra: Callable[[Any], dict] | None = None) -> str:
    out = io.StringIO()
    # B610: our own callable, not Django's QuerySet.extra
    names = [name for name, _ in columns] + (list(extra(rows[0]).keys()) if extra and rows else [])  # nosec B610
    writer = csv.writer(out)
    writer.writerow(names)
    for row in rows:
        values = [_cell(getter(row)) for _, getter in columns]
        if extra:
            # B610: our own callable, not Django's QuerySet.extra
            values += [_cell(v) for v in extra(row).values()]  # nosec B610
        writer.writerow(values)
    return out.getvalue()


def page_or_export(*, request: Request, db, ctx, search: Search, spec: Spec, page: int, count, fetch, list_name: str):
    """The list's page, or, with `?export=csv`, every matching row as CSV (up to
    EXPORT_LIMIT), recorded in the action log. `count(where=...)` and
    `fetch(where=..., order_by=..., limit=..., offset=...)` read through the list's own
    repository or service, so both stay inside the person's scope."""
    from fastapi.responses import Response

    from openclear.web.pagination import paginate

    where, order = spec.where(search), spec.order_by(search)
    if search.account_ids:
        from openclear.repositories.account_repo import AccountRepository

        accounts = AccountRepository(db, ctx.tenant_id, ctx.customer_id).get_many(search.account_ids)
        search.account_label = ", ".join(sorted(a.account_number for a in accounts))
    if request.query_params.get("export") == "csv":
        from openclear.services import audit_log_service

        rows = fetch(where=where, order_by=order, limit=EXPORT_LIMIT, offset=0)
        audit_log_service.record_action(
            db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="web", action="list.export",
            summary=f"Exported {len(rows)} {list_name.replace('_', ' ')} as CSV", resource_type=list_name,
            metadata={"list": list_name, "rows": len(rows), "filters": {k: v for k, v in search.values.items() if v}},
        )
        db.commit()
        return Response(to_csv(rows, spec.export_columns), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{list_name}.csv"'})
    return paginate(page=page, count_fn=lambda: count(where=where), list_fn=lambda **kw: fetch(where=where, order_by=order, **kw))


def resolve_and_redirect(form, db, ctx) -> str:
    """The list form's POST (/ui/list-search): the same filters as a query string for the list,
    with an account number typed turned into the ids of the accounts it matches, within what
    the person may see. Returns where to go."""
    from openclear.domain.account import Account
    from openclear.repositories.account_repo import AccountRepository

    target = str(form.get("list") or "")
    if target not in LISTS:
        target = LISTS[0]
    query = [(k, str(v).strip()[:100]) for k, v in form.multi_items()
             if k not in ("list", "account", "account_id", "csrf_token") and str(v).strip()]
    number = str(form.get("account") or "").strip()[:40]
    if number:
        repo = AccountRepository(db, ctx.tenant_id, ctx.customer_id)
        matches = repo.list(where=[or_(Account.account_number == number, Account.account_number.ilike(f"%{number}%"))],
                            order_by=[Account.account_number], limit=MAX_ACCOUNTS)
        query += [("account_id", str(a.id)) for a in matches] or [("account_id", "none")]
    return f"{target}?{urlencode(query)}" if query else target
