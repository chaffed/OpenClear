# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Query budgets (SCALE_PLAN.md S7): a scale regression check small enough to run on every
push. Counting SQL statements is deterministic where timings aren't: a change that adds a
query per row to a bulk import, or makes a list page query once per row shown, fails here
long before anyone notices it in production.

The budgets have some headroom over today's counts. If a deliberate change needs more,
raise the number in the same commit and say why."""

import io
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import event

from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.services import bulk_import_service
from openclear.workers import jobs
from tests.conftest import TenantFactory

# Statements per extra row of a bulk import (paid items with an exception each, which is
# the most expensive kind: matching, the exception, its score and the audit trail).
BULK_IMPORT_PER_ROW = 20  # 16 on SQLite when this was set (2026-10-09)
# Extra statements a list page may make for 30 more rows on it: none per row.
LIST_PAGE_GROWTH = 2


@contextmanager
def counting(engine):
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _import(db_session, tenant, account, users, rows: int, start: int) -> int:
    lines = ["account_number,check_number,presented_amount,presented_date"] + [
        f"{account.account_number},{start + n},{100 + n}.00,2026-03-02" for n in range(rows)
    ]
    with counting(db_session.get_bind()) as statements:
        bulk_import_service.receive_upload(
            db_session, tenant.id, kind=BulkUploadKind.PAID_ITEMS, file_format="tabular", filename="q.csv", content_type=None,
            uploaded_by_user_id=users["admin"].id, customer_id=None, stream=io.BytesIO(("\n".join(lines) + "\n").encode()),
        )
        jobs.drain(db_session.get_bind())
    return len(statements)


def test_bulk_import_queries_per_row(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="query-budget-bulk")
    _import(db_session, tenant, account, users, 5, start=1000)  # warm up caches
    small = _import(db_session, tenant, account, users, 10, start=2000)
    large = _import(db_session, tenant, account, users, 40, start=3000)
    per_row = (large - small) / 30
    print(f"bulk import: {per_row:.1f} statements per row")
    assert per_row <= BULK_IMPORT_PER_ROW


def _login(client, tenant, users):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})


def test_list_pages_do_not_query_per_row(client, db_session, tenant_factory):
    from datetime import date

    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item

    tenant, account, users = tenant_factory.make(slug="query-budget-pages")
    _login(client, tenant, users)

    def add(count, start):
        for n in range(count):
            ingest_paid_item(db_session, tenant.id, PaidItemSubmission(
                account_id=account.id, check_number=str(start + n), presented_amount=Decimal("10.00"), presented_date=date(2026, 3, 2),
            ))
        db_session.commit()

    pages = ["/ui/exceptions", "/ui/paid-items", "/ui/", "/api/v1/exceptions", "/api/v1/paid-items"]
    headers = None
    counts = {}
    for label, count, start in (("few", 5, 100), ("many", 30, 200)):
        add(count, start)
        for path in pages:
            if path.startswith("/api") and headers is None:
                from tests.conftest import login_headers

                headers = login_headers(client, tenant.slug, users["admin"].email)
            client.get(path, headers=headers if path.startswith("/api") else None)  # warm up
            with counting(db_session.get_bind()) as statements:
                assert client.get(path, headers=headers if path.startswith("/api") else None).status_code == 200
            counts.setdefault(path, {})[label] = len(statements)
    print(counts)
    for path, by_size in counts.items():
        assert by_size["many"] - by_size["few"] <= LIST_PAGE_GROWTH, f"{path} makes a query per row: {by_size}"
