# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Time OpenClear's hot paths against a database filled by seed_volume.py (SCALE_PLAN.md S0).

    OPENCLEAR_DATABASE_URL=postgresql+psycopg://openclear:openclear@127.0.0.1:55432/openclear_perf \\
        python scripts/perf/measure.py --ingest-rows 2000

Prints a Markdown report: page and API timings (through the real app, as perf-bank-0's
admin), bulk ingestion, one shared-model retrain, a full data export, audit chain
verification, and EXPLAIN ANALYZE for the busiest queries. It WRITES to the database
(the ingested checks, a model, an export), so use the same throwaway database.
"""

import argparse
import os
import statistics
import sys
import tempfile
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OPENCLEAR_ENVIRONMENT", "development")
os.environ["OPENCLEAR_RATE_LIMIT_PER_MINUTE"] = "1000000"
_scratch = Path(tempfile.mkdtemp(prefix="openclear-perf-"))
for name in ("ML_ARTIFACT_DIR", "CHECK_IMAGE_STORAGE_DIR", "TENANT_ASSET_STORAGE_DIR", "BULK_UPLOAD_STORAGE_DIR", "DATA_EXPORT_STORAGE_DIR"):
    os.environ.setdefault(f"OPENCLEAR_{name}", str(_scratch / name.lower()))

BANK = "perf-bank-0"
PASSWORD = "Perf-test-password-1"
_rows: list[tuple[str, str, str]] = []


def _record(section: str, what: str, seconds: float, note: str = "") -> None:
    value = f"{seconds * 1000:.2f} ms" if seconds < 0.01 else f"{seconds * 1000:,.0f} ms" if seconds < 10 else f"{seconds:,.1f} s"
    _rows.append((section, what, value, note))
    print(f"  {what}: {_rows[-1][2]} {note}", flush=True)


def _timed(fn):
    start = time.perf_counter()
    result = fn()
    return time.perf_counter() - start, result


def _pages(repeats: int) -> None:
    from fastapi.testclient import TestClient

    from openclear.main import create_app

    print("Pages and API", flush=True)
    with TestClient(create_app()) as client:
        client.get("/ui/login")
        csrf = client.cookies.get("csrf_token")
        login = client.post("/ui/login", data={"tenant_slug": BANK, "email": f"admin@{BANK}.example.com", "password": PASSWORD, "csrf_token": csrf, "next": "/ui/"}, follow_redirects=False)
        assert login.status_code == 303, login.text[:300]
        for path in ("/ui/", "/ui/exceptions", "/ui/exceptions?status=all", "/ui/exceptions?status=all&page=50",
                     "/ui/paid-items", "/ui/paid-items?page=200", "/ui/issued-items", "/ui/audit-log"):
            timings = []
            for _ in range(repeats):
                seconds, resp = _timed(lambda: client.get(path))
                assert resp.status_code == 200, (path, resp.status_code)
                timings.append(seconds)
            _record("Pages", f"GET {path}", statistics.median(timings), f"(median of {repeats}; max {max(timings) * 1000:,.0f} ms)")

        api = client.post("/api/v1/auth/login", json={"tenant_slug": BANK, "email": f"admin@{BANK}.example.com", "password": PASSWORD})
        headers = {"Authorization": f"Bearer {api.json()['access_token']}"}
        seconds, resp = _timed(lambda: client.get("/api/v1/exceptions", headers=headers))
        _record("API", "GET /api/v1/exceptions", seconds, f"({len(resp.json()):,} rows in one response, {len(resp.content) / 1e6:.1f} MB)")


def _ingest(session, tenant, rows: int) -> None:
    from openclear.domain.account import Account
    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_items_bulk
    from openclear.services import issued_item_service

    print("Bulk ingestion", flush=True)
    accounts = session.query(Account).filter(Account.tenant_id == tenant.id).limit(20).all()
    run = uuid.uuid4().hex[:6]
    issue = [
        issued_item_service.IssuedItemInput(account_id=accounts[n % len(accounts)].id, check_number=f"P{run}{n}", amount=Decimal("125.00"),
                                            payee_name="Perf Vendor", issue_date=date.today() - timedelta(days=5))
        for n in range(rows // 2)
    ]
    seconds, _ = _timed(lambda: issued_item_service.create_issued_items_bulk(session, tenant.id, issue, submitted_by_user_id=None))
    _record("Ingestion", f"{len(issue):,} issued checks (bulk)", seconds, f"({len(issue) / seconds:,.0f} rows/s)")

    # Half match an issued check; half are "not in file" and raise an exception (scored, notified).
    presented = [
        PaidItemSubmission(account_id=accounts[n % len(accounts)].id, check_number=f"P{run}{n}", presented_amount=Decimal("125.00"), presented_date=date.today())
        for n in range(rows)
    ]
    seconds, results = _timed(lambda: ingest_paid_items_bulk(session, tenant.id, presented))
    failed = sum(1 for r in results if not r.success)
    _record("Ingestion", f"{rows:,} presented checks (bulk, half raise exceptions)", seconds,
            f"({rows / seconds:,.0f} rows/s; {failed} rows failed; 50,000 would take ~{50_000 / (rows / seconds) / 60:,.1f} min)")


def _retrain(session) -> None:
    from openclear.config import get_settings
    from openclear.ml.train import train_model

    print("ML", flush=True)
    get_settings().ml_retrain_cooldown_seconds = 0
    seconds, result = _timed(lambda: train_model(session, "check"))
    session.commit()
    _record("ML", "Retrain the shared check model", seconds, f"({result.model_row.trained_from_decision_count:,} decisions)")


def _export(session, tenant, admin_id) -> None:
    from openclear.db.session import get_engine
    from openclear.domain.data_export_job import DataExportJob
    from openclear.services import data_export_service

    print("Export", flush=True)
    job = data_export_service.request_export(session, tenant.id, admin_id)
    session.commit()
    seconds, _ = _timed(lambda: data_export_service.run_export_job(get_engine(), job.id, tenant.id))
    session.expire_all()
    job = session.get(DataExportJob, job.id)
    _record("Export", "Full bank data export", seconds, f"(status {job.status.value})")


def _verify_chain(session, tenant) -> None:
    from openclear.domain.audit_log_entry import AuditLogEntry
    from openclear.services import audit_log_service

    print("Audit", flush=True)
    count = session.query(AuditLogEntry).filter(AuditLogEntry.tenant_id == tenant.id).count()
    seconds, result = _timed(lambda: audit_log_service.verify_chain(session, tenant.id))
    _record("Audit", "verify_chain", seconds, f"({count:,} entries; valid={result.valid})")


def _explain(session, tenant) -> None:
    from sqlalchemy import text

    print("Query plans", flush=True)
    sample = session.execute(text("SELECT account_id, check_number, presented_amount FROM paid_item WHERE tenant_id = :t LIMIT 1"), {"t": tenant.id}).one()
    queries = {
        "Duplicate-paid check (every presented check)": (
            "SELECT id FROM paid_item WHERE tenant_id = :t AND account_id = :a AND check_number = :c "
            "AND presented_amount = :amt AND settlement_status = 'PAID' LIMIT 1",
            {"t": tenant.id, "a": sample.account_id, "c": sample.check_number, "amt": sample.presented_amount},
        ),
        "Exceptions queue, first page": (
            "SELECT * FROM exception_item WHERE tenant_id = :t AND status IN ('OPEN', 'PENDING_APPROVAL') ORDER BY created_at LIMIT 25",
            {"t": tenant.id},
        ),
        "Expired-disposition sweep (all banks)": (
            "SELECT id FROM exception_item WHERE status IN ('OPEN', 'PENDING_APPROVAL') AND decision_deadline <= now()",
            {},
        ),
    }
    for label, (sql, params) in queries.items():
        plan = [row[0] for row in session.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sql), params)]
        execution = next((line for line in plan if line.startswith("Execution Time")), "")
        scan = next((line.strip() for line in plan if "Scan" in line), plan[0])
        _record("Query plans", label, float(execution.split(":")[1].split()[0]) / 1000 if execution else 0.0, f"— {scan[:110]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ingest-rows", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--skip", nargs="*", default=[], choices=["pages", "ingest", "ml", "export", "audit", "explain"])
    args = parser.parse_args()
    if not os.environ.get("OPENCLEAR_DATABASE_URL"):
        sys.exit("Set OPENCLEAR_DATABASE_URL to the database seed_volume.py filled.")

    import openclear.domain  # noqa: F401
    from sqlalchemy import func, select

    from openclear.db.session import get_session_factory
    from openclear.domain.exception_item import ExceptionItem
    from openclear.domain.paid_item import PaidItem
    from openclear.domain.tenant import Tenant
    from openclear.domain.user import User

    session = get_session_factory()()
    tenant = session.execute(select(Tenant).where(Tenant.slug == BANK)).scalar_one()
    admin = session.execute(select(User).where(User.email == f"admin@{BANK}.example.com")).scalar_one()
    totals = {
        "banks": session.execute(select(func.count()).select_from(Tenant)).scalar_one(),
        "paid items": session.execute(select(func.count()).select_from(PaidItem)).scalar_one(),
        "exceptions": session.execute(select(func.count()).select_from(ExceptionItem)).scalar_one(),
    }
    print("Database: " + ", ".join(f"{v:,} {k}" for k, v in totals.items()), flush=True)

    if "pages" not in args.skip:
        _pages(args.repeats)
    if "explain" not in args.skip:
        _explain(session, tenant)
    if "audit" not in args.skip:
        _verify_chain(session, tenant)
    if "ml" not in args.skip:
        _retrain(session)
    if "export" not in args.skip:
        _export(session, tenant, admin.id)
    if "ingest" not in args.skip:
        _ingest(session, tenant, args.ingest_rows)
    session.close()

    print("\n### Results\n")
    print("Database: " + ", ".join(f"{v:,} {k}" for k, v in totals.items()) + "\n")
    print("| Area | What | Time | Notes |\n|------|------|------|-------|")
    for section, what, value, note in _rows:
        print(f"| {section} | {what} | {value} | {note} |")


if __name__ == "__main__":
    main()
