# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Fill a database with realistic volume for performance measurement (SCALE_PLAN.md S0).

    POSPAY_DATABASE_URL=postgresql+psycopg://pospay:pospay@127.0.0.1:55432/pospay_perf \\
        python scripts/perf/seed_volume.py --banks 5 --items-per-bank 100000

Use a throwaway database: it runs `alembic upgrade head`, then ADDS data (banks named
perf-bank-N) without touching anything else. Every bank's admin signs in as
admin@perf-bank-N.example.com with the password printed at the end.

Rows are bulk-inserted rather than sent through the ingestion services (which would take
hours at this size), so they're shaped like real data but weren't produced by the matching
rules: per bank, accounts with issued checks, presented checks (most matching an issued
check), exceptions for a share of those, human decisions on most exceptions (with ML
features, so training has data), ACH debits, and a signed audit trail.
"""

import argparse
import os
import random
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("POSPAY_ENVIRONMENT", "development")

PASSWORD = "Perf-test-password-1"
_BATCH = 5_000


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--banks", type=int, default=5)
    parser.add_argument("--accounts-per-bank", type=int, default=50)
    parser.add_argument("--items-per-bank", type=int, default=100_000, help="presented checks per bank")
    parser.add_argument("--exception-rate", type=float, default=0.03, help="share of presented checks that raise an exception")
    parser.add_argument("--decided-rate", type=float, default=0.9, help="share of exceptions already decided")
    parser.add_argument("--ach-per-bank", type=int, default=20_000)
    parser.add_argument("--audit-entries-per-bank", type=int, default=2_000)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def _insert(session, model, rows: list[dict]) -> None:
    from sqlalchemy import insert

    for start in range(0, len(rows), _BATCH):
        session.execute(insert(model), rows[start : start + _BATCH])


def _check_features(rng: random.Random, exception_types: list[str]) -> dict:
    return {
        "amount_delta_abs": round(rng.uniform(0, 5000), 2) if "amount_mismatch" in exception_types else 0.0,
        "amount_delta_pct": round(rng.uniform(0, 3), 3) if "amount_mismatch" in exception_types else 0.0,
        "days_since_issue": rng.randint(0, 400),
        "payee_similarity_score": round(rng.uniform(40, 100), 1),
        "ocr_confidence": round(rng.uniform(0, 1), 2),
        "is_duplicate_paid": "duplicate_paid" in exception_types,
        "is_stopped": "stopped" in exception_types,
        "is_not_in_file": "not_in_file" in exception_types,
        "is_voided": "voided" in exception_types,
    }


def _seed_bank(session, index: int, args, rng: random.Random) -> dict:
    from pospay.domain.account import Account
    from pospay.domain.ach_transaction import AchMatchStatus, AchTransaction, AchTransactionSource, AchTransactionType
    from pospay.domain.decision import Decision, DecisionOutcome
    from pospay.domain.exception_item import ExceptionItem, ExceptionStatus
    from pospay.domain.issued_item import IssuedItem, IssuedItemSource, IssuedItemStatus
    from pospay.domain.paid_item import PaidItem, PaidItemMatchStatus, PaidItemSettlementStatus, PaidItemSource
    from pospay.services import audit_log_service, provisioning_service

    slug = f"perf-bank-{index}"
    identity = provisioning_service.create_tenant_with_admin(
        session, tenant_name=f"Perf Bank {index}", tenant_slug=slug, admin_email=f"admin@{slug}.example.com", admin_password=PASSWORD
    )
    tenant_id = identity.tenant.id
    admin_id = identity.admin_user.id
    session.flush()

    accounts = [{"id": uuid.uuid4(), "tenant_id": tenant_id, "account_number": f"{index:03d}{n:06d}", "name": f"Operating {n}"} for n in range(args.accounts_per_bank)]
    _insert(session, Account, accounts)
    account_ids = [a["id"] for a in accounts]

    today = date.today()
    issued, paid, exceptions, decisions = [], [], [], []
    exception_type_choices = ["amount_mismatch", "not_in_file", "stale_dated", "payee_mismatch", "duplicate_paid", "voided"]
    for n in range(args.items_per_bank):
        account_id = account_ids[n % len(account_ids)]
        check_number = str(100000 + n)
        amount = Decimal(rng.randint(500, 500_000)) / 100
        issue_date = today - timedelta(days=rng.randint(1, 365))
        presented_date = issue_date + timedelta(days=rng.randint(0, 60))
        issued_id = uuid.uuid4()
        is_exception = rng.random() < args.exception_rate
        issued.append({
            "id": issued_id, "tenant_id": tenant_id, "account_id": account_id, "check_number": check_number, "amount": amount,
            "payee_name": f"Vendor {rng.randint(1, 5000)}", "issue_date": issue_date, "source": IssuedItemSource.API,
            "status": IssuedItemStatus.OUTSTANDING if is_exception else IssuedItemStatus.PAID,
        })
        paid_id = uuid.uuid4()
        paid.append({
            "id": paid_id, "tenant_id": tenant_id, "account_id": account_id, "check_number": check_number,
            "presented_amount": amount + (Decimal(rng.randint(1, 90000)) / 100 if is_exception else 0),
            "presented_date": presented_date, "source": PaidItemSource.API, "matched_issued_item_id": issued_id,
            "match_status": PaidItemMatchStatus.EXCEPTION if is_exception else PaidItemMatchStatus.MATCHED,
            "settlement_status": PaidItemSettlementStatus.PENDING if is_exception else PaidItemSettlementStatus.PAID,
        })
        if is_exception:
            types = rng.sample(exception_type_choices, k=rng.randint(1, 2))
            decided = rng.random() < args.decided_rate
            outcome = DecisionOutcome.PAY if rng.random() < 0.8 else DecisionOutcome.RETURN
            exception_id = uuid.uuid4()
            created_at = datetime.combine(presented_date, datetime.min.time(), tzinfo=timezone.utc)
            exceptions.append({
                "id": exception_id, "tenant_id": tenant_id, "network_code": "check", "source_item_id": paid_id,
                "related_reference_id": issued_id, "exception_types": ",".join(types),
                "status": (ExceptionStatus.PAY if outcome == DecisionOutcome.PAY else ExceptionStatus.RETURN) if decided else ExceptionStatus.OPEN,
                "ml_score": round(rng.random(), 4), "created_at": created_at,
            })
            if decided:
                decisions.append({
                    "id": uuid.uuid4(), "tenant_id": tenant_id, "exception_item_id": exception_id, "outcome": outcome,
                    "reason_code": "perf", "decided_by_user_id": admin_id, "decided_at": created_at + timedelta(hours=4),
                    "features_json": _check_features(rng, types),
                })
    _insert(session, IssuedItem, issued)
    _insert(session, PaidItem, paid)
    _insert(session, ExceptionItem, exceptions)
    _insert(session, Decision, decisions)

    ach = [{
        "id": uuid.uuid4(), "tenant_id": tenant_id, "account_id": account_ids[n % len(account_ids)],
        "originator_id": f"ORIG{rng.randint(1, 300):04d}", "originator_name": "Originator", "receiver_id": f"R{rng.randint(1, 50)}",
        "amount": Decimal(rng.randint(100, 2_000_000)) / 100, "transaction_type": AchTransactionType.DEBIT, "sec_code": rng.choice(["PPD", "CCD", "WEB"]),
        "trace_number": f"{index:03d}{n:012d}", "effective_date": today - timedelta(days=rng.randint(0, 365)), "source": AchTransactionSource.API,
        "match_status": AchMatchStatus.MATCHED,
    } for n in range(args.ach_per_bank)]
    _insert(session, AchTransaction, ach)
    session.commit()

    for n in range(args.audit_entries_per_bank):
        audit_log_service.record_action(
            session, tenant_id, actor_user_id=admin_id, channel="web", action="perf.seed", summary=f"Perf seed entry {n}",
            resource_type="tenant", resource_id=tenant_id,
        )
        if n % 500 == 499:
            session.commit()
    session.commit()
    return {"slug": slug, "items": len(paid), "exceptions": len(exceptions), "decisions": len(decisions), "ach": len(ach)}


def main() -> None:
    args = _args()
    if not os.environ.get("POSPAY_DATABASE_URL"):
        sys.exit("Set POSPAY_DATABASE_URL to a throwaway database first.")

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")

    import pospay.domain  # noqa: F401 -- registers every mapper
    from pospay.db.session import get_session_factory

    rng = random.Random(args.seed)
    session = get_session_factory()()
    try:
        from sqlalchemy import select

        from pospay.domain.tenant import Tenant

        existing = set(session.execute(select(Tenant.slug).where(Tenant.slug.like("perf-bank-%"))).scalars())
        start = time.perf_counter()
        for index in range(args.banks):
            if f"perf-bank-{index}" in existing:
                print(f"perf-bank-{index}: already there, skipped")
                continue
            bank_start = time.perf_counter()
            summary = _seed_bank(session, index, args, rng)
            print(f"{summary['slug']}: {summary['items']:,} checks, {summary['exceptions']:,} exceptions, "
                  f"{summary['decisions']:,} decisions, {summary['ach']:,} ACH ({time.perf_counter() - bank_start:.0f}s)")
        print(f"Done in {time.perf_counter() - start:.0f}s. Admins sign in as admin@perf-bank-N.example.com / {PASSWORD}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
