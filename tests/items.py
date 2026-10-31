# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Making customers, exceptions and decisions for tests that need them (moved here from the
per-customer ML tests, which SIMPLIFY_PLAN.md SP2 removes)."""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from openclear.db.tenancy import TenantContext
from openclear.domain.decision import Decision, DecisionOutcome
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import account_service, customer_service, decision_service, issued_item_service

__all__ = ["TenantContext", "_decide", "_make_customer_with_account", "_make_exception", "_seed_decisions"]


def _make_customer_with_account(db_session, tenant, customer_number: str):
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number=customer_number, name=f"Customer {customer_number}")
    )
    account = account_service.create_account(
        db_session,
        tenant.id,
        account_service.AccountInput(account_number=f"{customer_number}-ACCT", name="Acct", customer_id=customer.id),
    )
    db_session.commit()
    return customer, account


def _make_exception(db_session, tenant, account, users, check_number: str, issued_amount: str, presented_amount: str):
    issued_item_service.create_issued_item(
        db_session,
        tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id,
            check_number=check_number,
            amount=Decimal(issued_amount),
            payee_name="Some Vendor",
            issue_date=date(2026, 1, 1),
        ),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()

    paid_item = ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(
            account_id=account.id,
            check_number=check_number,
            presented_amount=Decimal(presented_amount),
            presented_date=date(2026, 1, 10),
        ),
    )
    db_session.commit()

    exceptions = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)
    return exceptions[0]


def _decide(db_session, tenant, users, exception, outcome: DecisionOutcome):
    ctx = TenantContext(
        tenant_id=tenant.id,
        user_id=users["approver"].id,
        security_group_id=uuid.uuid4(),
        permissions=frozenset(),
        tenant_slug=tenant.slug,
        tenant_name=tenant.name,
        accent_color=None,
        has_logo=False,
        has_favicon=False,
        customer_id=None,
        customer_name=None,
    )
    result = decision_service.decide(
        db_session, tenant.id, exception.id, ctx, outcome=outcome, reason_code="test", notes=None
    )
    db_session.commit()
    return result


def _seed_decisions(db_session, tenant, account, users, count: int, prefix: str):
    # One second apart, like real decisions: SQLite stores whole seconds, and decisions
    # that tie would make training's "most recent" holdout depend on tie order.
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    for i in range(count):
        exception = _make_exception(
            db_session, tenant, account, users, f"{prefix}{i:02d}", "100.00", "999.00" if i % 2 == 0 else "888.00"
        )
        outcome = DecisionOutcome.RETURN if i % 2 == 0 else DecisionOutcome.PAY
        result = _decide(db_session, tenant, users, exception, outcome)
        assert result.error is None
        decision = db_session.query(Decision).filter(Decision.exception_item_id == exception.id).one()
        decision.decided_at = start + timedelta(seconds=i)
        db_session.commit()


def shows_number(page: str, number: str) -> bool:
    """Whether a page shows this account or check number as a number of its own. Every page's head
    carries Unix timestamps (base.html's session-expiry meta tags), so a plain `"4001" in page`
    is true whenever the clock's digits happen to contain it. Nor inside an id: a UUID's groups are
    hex, and one in 4,096 has "-4001-" in its third (REVIEW_1_33_PLAN.md K6)."""
    return re.search(rf"(?<![0-9A-Fa-f-]){re.escape(number)}(?![0-9A-Fa-f-])", page) is not None
