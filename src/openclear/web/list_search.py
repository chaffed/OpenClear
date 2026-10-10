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

FIELDS = ("q", "number", "amount_min", "amount_max", "date_from", "date_to", "account", "sort", "dir")
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
    return Search({name: request.query_params.get(name, "").strip()[:100] for name in FIELDS + extra})


@dataclass
class Spec:
    """One list's filters and sort columns. `conditions` turns a Search into SQL
    conditions; `sorts` maps a sort name to a column; `default_sort` is (column, descending)."""

    conditions: Callable[[Search], list]
    sorts: dict[str, Any]
    default_order: Callable[[], Any]
    export_columns: list[tuple[str, Callable[[Any], Any]]]

    def where(self, search: Search) -> list:
        return self.conditions(search)

    def order_by(self, search: Search):
        column = self.sorts.get(search.get("sort"))
        if column is None:
            return self.default_order()
        return [column.desc() if search.get("dir") == "desc" else column.asc()]


def _account_ids(number: str):
    from openclear.domain.account import Account

    return select(Account.id).where(or_(Account.account_number == number, Account.account_number.ilike(f"%{number}%")))


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
        if s.get("account"):
            out.append(I.account_id.in_(_account_ids(s.get("account"))))
        if q := s.get("q"):
            amount = parse_amount(q)
            out.append(or_(I.check_number == q, I.payee_name.ilike(f"%{q}%"), *([I.amount == amount] if amount is not None else [])))
        return out

    return Spec(conditions, {"number": I.check_number, "amount": I.amount, "payee": I.payee_name, "date": I.issue_date,
                             "status": I.status},
                lambda: [I.created_at.desc()],
                [("check_number", lambda r: r.check_number), ("amount", lambda r: r.amount), ("payee", lambda r: r.payee_name),
                 ("issue_date", lambda r: r.issue_date), ("status", lambda r: r.status.value), ("account_id", lambda r: r.account_id)])


def paid_items_spec() -> Spec:
    from openclear.domain.paid_item import PaidItem as P

    def conditions(s: Search) -> list:
        out = (_range(P.presented_amount, s, "amount_min", "amount_max", parse_amount)
               + _range(P.presented_date, s, "date_from", "date_to", _date))
        if s.get("number"):
            out.append(P.check_number == s.get("number"))
        if s.get("account"):
            out.append(P.account_id.in_(_account_ids(s.get("account"))))
        if q := s.get("q"):
            amount = parse_amount(q)
            out.append(or_(P.check_number == q, *([P.presented_amount == amount] if amount is not None else [])))
        return out

    return Spec(conditions, {"number": P.check_number, "amount": P.presented_amount, "date": P.presented_date,
                             "match": P.match_status, "settlement": P.settlement_status},
                lambda: [P.created_at.desc()],
                [("check_number", lambda r: r.check_number), ("amount", lambda r: r.presented_amount),
                 ("presented_date", lambda r: r.presented_date), ("match_status", lambda r: r.match_status.value),
                 ("settlement_status", lambda r: r.settlement_status.value), ("account_id", lambda r: r.account_id)])


def ach_transactions_spec() -> Spec:
    from openclear.domain.ach_transaction import AchTransaction as T

    def conditions(s: Search) -> list:
        out = _range(T.amount, s, "amount_min", "amount_max", parse_amount) + _range(T.effective_date, s, "date_from", "date_to", _date)
        if s.get("number"):
            out.append(T.trace_number == s.get("number"))
        if s.get("account"):
            out.append(T.account_id.in_(_account_ids(s.get("account"))))
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
                 ("match_status", lambda r: r.match_status.value), ("account_id", lambda r: r.account_id)])


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
        if s.get("account"):
            checks.append(P.account_id.in_(_account_ids(s.get("account"))))
            ach.append(T.account_id.in_(_account_ids(s.get("account"))))
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
                 ("ml_score", lambda r: r.ml_score)])


# --- Export -----------------------------------------------------------------------------------

def _cell(value: Any) -> Any:
    """A spreadsheet must never run a cell as a formula (CSV injection)."""
    if value is None:
        return ""
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(rows, columns: list[tuple[str, Callable[[Any], Any]]], extra: Callable[[Any], dict] | None = None) -> str:
    out = io.StringIO()
    names = [name for name, _ in columns] + (list(extra(rows[0]).keys()) if extra and rows else [])
    writer = csv.writer(out)
    writer.writerow(names)
    for row in rows:
        values = [_cell(getter(row)) for _, getter in columns]
        if extra:
            values += [_cell(v) for v in extra(row).values()]
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
