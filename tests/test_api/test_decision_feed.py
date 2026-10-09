# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The decision feed (SDK_PLAN.md P0; services/decision_feed_service.py)."""

import csv
import io
from datetime import date
from decimal import Decimal

from openclear.domain.ach_transaction import AchTransactionType
from openclear.domain.customer_disposition_setting import DispositionMode
from openclear.domain.decision import DecisionOutcome
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import auto_disposition_service
from openclear.workers import tasks
from tests.conftest import login_headers
from tests.test_auth.test_sweep_expired_dispositions_job import _expired_exception, _make_customer
from tests.test_ml.test_per_customer_ml import _decide, _make_exception


def _ach_exception(db_session, tenant, account, trace):
    txn = ingest_ach_transaction(db_session, tenant.id, AchTransactionSubmission(
        account_id=account.id, originator_id="UNKNOWN1", originator_name="=Unknown Co", amount=Decimal("75.00"),
        transaction_type=AchTransactionType.DEBIT, sec_code="WEB", trace_number=trace, effective_date=date(2026, 1, 12),
    ))
    db_session.commit()
    return ExceptionRepository(db_session, tenant.id).list(source_item_id=txn.id)[0]


def test_decisions_come_out_in_order_with_what_a_core_needs(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="feed-order")
    first = _make_exception(db_session, tenant, account, users, "101", "100.00", "999.00")
    second = _ach_exception(db_session, tenant, account, "TRACE-1")
    third = _make_exception(db_session, tenant, account, users, "102", "100.00", "888.00")
    _decide(db_session, tenant, users, first, DecisionOutcome.RETURN)
    _decide(db_session, tenant, users, second, DecisionOutcome.PAY)
    _decide(db_session, tenant, users, third, DecisionOutcome.PAY)
    headers = login_headers(client, tenant.slug, users["admin"].email)

    resp = client.get("/api/v1/decisions", headers=headers)
    feed = resp.json()
    assert resp.headers["x-latest-sequence"] == "3"
    assert [(e["sequence"], e["network"], e["outcome"]) for e in feed] == [(1, "check", "return"), (2, "ach", "pay"), (3, "check", "pay")]
    check = feed[0]
    assert (check["account_number"], check["check_number"], check["amount"], check["presented_date"]) == (account.account_number, "101", "999.00", "2026-01-10")
    assert check["decided_by"] == users["approver"].email and check["source"] == "human"
    assert (feed[1]["trace_number"], feed[1]["amount"], feed[1]["effective_date"]) == ("TRACE-1", "75.00", "2026-01-12")

    after = client.get("/api/v1/decisions?after=1&limit=1", headers=headers)
    assert [e["sequence"] for e in after.json()] == [2] and 'rel="next"' in after.headers["link"]

    report = client.get("/api/v1/decisions/feed.csv?after=0", headers=headers)
    rows = list(csv.DictReader(io.StringIO(report.text)))
    assert [r["sequence"] for r in rows] == ["1", "2", "3"] and rows[1]["originator_name"] == "'=Unknown Co"


def test_automatic_decisions_are_numbered_and_training_examples_are_not(monkeypatch, session_factory, client, db_session, tenant_factory):
    from tests.test_ml.test_bank_model_choice import _submit_fraud_example

    tenant, account, users = tenant_factory.make(slug="feed-auto")
    customer, customer_account = _make_customer(db_session, tenant)
    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "check", mode=DispositionMode.FIXED_PAY, response_window_hours=1, default_ach_return_reason_id=None,
    )
    db_session.commit()
    _submit_fraud_example(db_session, tenant, account, users, "FRAUD-1")  # a decision, but not an item
    _expired_exception(db_session, tenant, customer_account, users, "AUTO-1")
    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    tasks.sweep_expired_dispositions_job()

    feed = client.get("/api/v1/decisions", headers=login_headers(client, tenant.slug, users["admin"].email)).json()
    assert [(e["sequence"], e["check_number"], e["source"], e["customer_number"]) for e in feed] == [(1, "AUTO-1", "auto_default", customer.customer_number)]


def test_each_bank_numbers_its_own_and_sees_only_its_own(client, db_session, tenant_factory):
    a, a_account, a_users = tenant_factory.make(slug="feed-bank-a")
    b, b_account, b_users = tenant_factory.make(slug="feed-bank-b")
    _decide(db_session, a, a_users, _make_exception(db_session, a, a_account, a_users, "1", "1.00", "2.00"), DecisionOutcome.PAY)
    _decide(db_session, b, b_users, _make_exception(db_session, b, b_account, b_users, "1", "1.00", "2.00"), DecisionOutcome.PAY)
    feed_b = client.get("/api/v1/decisions", headers=login_headers(client, b.slug, b_users["admin"].email)).json()
    assert [(e["sequence"], e["account_number"]) for e in feed_b] == [(1, b_account.account_number)]


def test_acknowledging_moves_forward_and_shows_lag(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="feed-ack")
    for n in range(3):
        _decide(db_session, tenant, users, _make_exception(db_session, tenant, account, users, f"A{n}", "1.00", "2.00"), DecisionOutcome.PAY)
    headers = login_headers(client, tenant.slug, users["admin"].email)

    assert client.post("/api/v1/decisions/acknowledge", headers=headers, json={"consumer": "core", "through_sequence": 2}).status_code == 200
    [core] = client.get("/api/v1/decisions/consumers", headers=headers).json()
    assert (core["consumer"], core["through_sequence"], core["behind"]) == ("core", 2, 1)
    backwards = client.post("/api/v1/decisions/acknowledge", headers=headers, json={"consumer": "core", "through_sequence": 1})
    beyond = client.post("/api/v1/decisions/acknowledge", headers=headers, json={"consumer": "core", "through_sequence": 9})
    assert backwards.status_code == beyond.status_code == 422
