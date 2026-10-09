# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The decision feed (SDK_PLAN.md P0): every pay or return decision a bank makes, in
order, with what its core needs to act on it, so the core pays or returns the item before
the deadline without reading exceptions one by one.

Each live decision gets the bank's next `feed_sequence` (1, 2, 3...) when it's made. On
Postgres the number is taken under a per-bank transaction lock, so decisions become
visible in the order they're numbered: a consumer reading "everything after N" can't skip
one that commits a moment late. Fraud-training examples aren't items and get no number.

A consumer (the core, a middleware job) reads `GET /api/v1/decisions?after=N`, acts on
each decision, and confirms with `POST /api/v1/decisions/acknowledge`, so the bank can see
a consumer falling behind."""

import csv
import hashlib
import io
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.domain.account import Account
from openclear.domain.ach_transaction import AchTransaction
from openclear.domain.customer import Customer
from openclear.domain.decision import Decision, DecisionFeedCursor
from openclear.domain.exception_item import ExceptionItem, ExceptionItemSource
from openclear.domain.paid_item import PaidItem
from openclear.domain.user import User

MAX_CONSUMER_NAME = 100


def _lock_key(tenant_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"openclear.decision_feed:{tenant_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def assign_sequence(session: Session, decision: Decision, exception: ExceptionItem) -> None:
    """Numbers a new live decision. Call after adding it, in the same transaction."""
    if exception.source == ExceptionItemSource.TRAINING_BACKFILL:
        return
    if session.get_bind().dialect.name == "postgresql":
        session.execute(select(func.pg_advisory_xact_lock(_lock_key(exception.tenant_id))))
    latest = session.execute(
        select(func.max(Decision.feed_sequence)).where(Decision.tenant_id == exception.tenant_id)
    ).scalar_one()
    decision.feed_sequence = (latest or 0) + 1
    session.flush()


def latest_sequence(session: Session, tenant_id: uuid.UUID) -> int:
    return session.execute(select(func.max(Decision.feed_sequence)).where(Decision.tenant_id == tenant_id)).scalar_one() or 0


def read_feed(
    session: Session, tenant_id: uuid.UUID, *, after: int = 0, limit: int = 500, customer_id: uuid.UUID | None = None
) -> list[dict]:
    """Decisions with feed_sequence > `after`, in order, at most `limit`."""
    query = (
        select(Decision, ExceptionItem)
        .join(ExceptionItem, ExceptionItem.id == Decision.exception_item_id)
        .where(Decision.tenant_id == tenant_id, Decision.feed_sequence.is_not(None), Decision.feed_sequence > after)
        .order_by(Decision.feed_sequence)
        .limit(limit)
    )
    if customer_id is not None:
        query = query.where(ExceptionItem.customer_id == customer_id)
    rows = session.execute(query).all()

    # The items, accounts, customers and deciders for the whole page, a query each.
    check_ids = [e.source_item_id for _, e in rows if e.network_code == "check"]
    ach_ids = [e.source_item_id for _, e in rows if e.network_code == "ach"]
    checks = {p.id: p for p in session.execute(select(PaidItem).where(PaidItem.id.in_(check_ids))).scalars()} if check_ids else {}
    debits = {a.id: a for a in session.execute(select(AchTransaction).where(AchTransaction.id.in_(ach_ids))).scalars()} if ach_ids else {}
    account_ids = {i.account_id for i in [*checks.values(), *debits.values()]}
    accounts = {a.id: a for a in session.execute(select(Account).where(Account.id.in_(account_ids))).scalars()} if account_ids else {}
    customer_ids = {a.customer_id for a in accounts.values() if a.customer_id}
    customers = {c.id: c for c in session.execute(select(Customer).where(Customer.id.in_(customer_ids))).scalars()} if customer_ids else {}
    user_ids = {d.decided_by_user_id for d, _ in rows if d.decided_by_user_id}
    users = {u.id: u for u in session.execute(select(User).where(User.id.in_(user_ids))).scalars()} if user_ids else {}

    feed = []
    for decision, exception in rows:
        item = checks.get(exception.source_item_id) if exception.network_code == "check" else debits.get(exception.source_item_id)
        account = accounts.get(item.account_id) if item else None
        customer = customers.get(account.customer_id) if account and account.customer_id else None
        decider = users.get(decision.decided_by_user_id)
        entry = {
            "sequence": decision.feed_sequence,
            "decision_id": str(decision.id),
            "exception_id": str(exception.id),
            "network": exception.network_code,
            "outcome": decision.outcome.value,
            "reason_code": decision.reason_code,
            "return_transaction_code": decision.return_transaction_code,
            "source": decision.source.value,
            "decided_by": decider.email if decider else None,
            "decided_at": decision.decided_at.isoformat() if decision.decided_at else None,
            "account_number": account.account_number if account else None,
            "account_name": account.name if account else None,
            "customer_number": customer.customer_number if customer else None,
        }
        if exception.network_code == "check" and item is not None:
            entry.update(check_number=item.check_number, amount=str(item.presented_amount), presented_date=item.presented_date.isoformat())
        elif exception.network_code == "ach" and item is not None:
            entry.update(
                trace_number=item.trace_number, amount=str(item.amount), effective_date=item.effective_date.isoformat(),
                originator_id=item.originator_id, originator_name=item.originator_name, sec_code=item.sec_code,
            )
        feed.append(entry)
    return feed


CSV_COLUMNS = (
    "sequence", "network", "outcome", "reason_code", "return_transaction_code", "account_number", "customer_number",
    "check_number", "trace_number", "amount", "presented_date", "effective_date", "originator_id", "originator_name",
    "sec_code", "source", "decided_by", "decided_at", "decision_id", "exception_id",
)


def to_csv(feed: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for entry in feed:
        writer.writerow({k: _csv_safe(v) for k, v in entry.items()})
    return out.getvalue()


def _csv_safe(value):
    # Originator names come from ACH files; don't let a spreadsheet run one.
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def acknowledge(session: Session, tenant_id: uuid.UUID, *, consumer: str, through_sequence: int, user_id: uuid.UUID | None) -> DecisionFeedCursor:
    """Records that `consumer` has processed the feed through `through_sequence`. It can
    only move forward, and not past the latest decision."""
    consumer = (consumer or "").strip()
    if not consumer or len(consumer) > MAX_CONSUMER_NAME:
        raise ValueError(f"consumer must be 1 to {MAX_CONSUMER_NAME} characters.")
    if through_sequence < 0 or through_sequence > latest_sequence(session, tenant_id):
        raise ValueError("through_sequence is beyond the latest decision.")
    cursor = session.execute(
        select(DecisionFeedCursor).where(DecisionFeedCursor.tenant_id == tenant_id, DecisionFeedCursor.consumer == consumer)
    ).scalar_one_or_none()
    if cursor is None:
        cursor = DecisionFeedCursor(tenant_id=tenant_id, consumer=consumer, through_sequence=0)
        session.add(cursor)
    if through_sequence < cursor.through_sequence:
        raise ValueError(f"{consumer} has already acknowledged through {cursor.through_sequence}; it can't go back.")
    cursor.through_sequence = through_sequence
    cursor.updated_by_user_id = user_id
    cursor.updated_at = datetime.now(timezone.utc)
    session.flush()
    return cursor


def consumers(session: Session, tenant_id: uuid.UUID) -> list[dict]:
    latest = latest_sequence(session, tenant_id)
    return [
        {"consumer": c.consumer, "through_sequence": c.through_sequence, "behind": latest - c.through_sequence,
         "updated_at": c.updated_at.isoformat() if c.updated_at else None}
        for c in session.execute(
            select(DecisionFeedCursor).where(DecisionFeedCursor.tenant_id == tenant_id).order_by(DecisionFeedCursor.consumer)
        ).scalars()
    ]
