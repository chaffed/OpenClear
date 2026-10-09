# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The decision outbox (SDK_PLAN.md P3; services/decision_outbox_service.py): files on a
schedule, batches a core downloads and acknowledges, the decision_batch.ready webhook, and
the NACHA return file layout (checked against Moov's validator when one is running)."""

import hashlib
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from openclear.domain.ach_return_reason import AchReturnReason
from openclear.domain.ach_transaction import AchTransactionType
from openclear.domain.decision import DecisionOutcome
from openclear.domain.webhook import WebhookDelivery
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import decision_outbox_service, decision_service, webhook_service
from tests.conftest import login_headers
from tests.test_ml.test_per_customer_ml import _decide, _make_exception

NACHA = {"routing_number": "231380104", "bank_name": "Acme Bank", "operator_routing_number": "091000080", "operator_name": "FED"}
MOOV = os.environ.get("OPENCLEAR_TEST_MOOV_ACH_URL", "http://localhost:18080")


def _ach_return(db_session, tenant, account, users, trace, amount="75.00", code="R01"):
    txn = ingest_ach_transaction(db_session, tenant.id, AchTransactionSubmission(
        account_id=account.id, originator_id="1234567890", originator_name="Acme Utility", amount=Decimal(amount),
        transaction_type=AchTransactionType.DEBIT, sec_code="PPD", trace_number=trace, effective_date=date(2026, 10, 8),
    ))
    db_session.commit()
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=txn.id)[0]
    reason = db_session.execute(select(AchReturnReason).where(AchReturnReason.tenant_id == tenant.id,
                                                              AchReturnReason.transaction_code == code)).scalars().first()
    from tests.test_ml.test_per_customer_ml import TenantContext

    ctx = TenantContext(tenant_id=tenant.id, user_id=users["approver"].id, security_group_id=uuid.uuid4(), permissions=frozenset(),
                        tenant_slug=tenant.slug, tenant_name=tenant.name, accent_color=None, has_logo=False, has_favicon=False,
                        customer_id=None, customer_name=None)
    decision_service.decide(db_session, tenant.id, exception.id, ctx, outcome=DecisionOutcome.RETURN, reason_code="x", notes=None,
                            ach_return_reason_id=reason.id)
    db_session.commit()


def test_batches_follow_the_feed_and_send_a_webhook(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="outbox-csv")
    webhook_service.create_endpoint(db_session, tenant.id, url="https://hooks.example.com/x", event_types=["decision_batch.ready"])
    for n in range(3):
        _decide(db_session, tenant, users, _make_exception(db_session, tenant, account, users, f"O{n}", "1.00", "2.00"), DecisionOutcome.PAY)

    first = decision_outbox_service.run(db_session, tenant.id)
    db_session.commit()
    assert (first.from_sequence, first.to_sequence, first.decision_count, first.layout) == (1, 3, 3, "openclear_csv")
    assert decision_outbox_service.run(db_session, tenant.id) is None  # nothing new
    _decide(db_session, tenant, users, _make_exception(db_session, tenant, account, users, "O9", "1.00", "2.00"), DecisionOutcome.RETURN)
    second = decision_outbox_service.run(db_session, tenant.id)
    db_session.commit()
    assert (second.from_sequence, second.to_sequence) == (4, 4)
    events = [d.event_type for d in db_session.execute(select(WebhookDelivery)).scalars()]
    assert events.count("decision_batch.ready") == 2

    headers = login_headers(client, tenant.slug, users["admin"].email)
    listed = client.get("/api/v1/decision-batches?unacknowledged_only=true", headers=headers)
    assert [b["from_sequence"] for b in listed.json()] == [1, 4] and listed.headers["x-total-count"] == "2"
    file = client.get(f"/api/v1/decision-batches/{first.id}/file", headers=headers)
    assert file.headers["x-content-sha256"] == hashlib.sha256(file.content).hexdigest() == first.sha256
    assert file.text.startswith("sequence,") and file.text.count("\n") == 4

    # Acknowledging out of order only moves the outbox consumer as far as it's contiguous.
    client.post(f"/api/v1/decision-batches/{second.id}/acknowledge", headers=headers)
    consumers = {c["consumer"]: c for c in client.get("/api/v1/decisions/consumers", headers=headers).json()}
    assert "outbox" not in consumers
    client.post(f"/api/v1/decision-batches/{first.id}/acknowledge", headers=headers)
    consumers = {c["consumer"]: c for c in client.get("/api/v1/decisions/consumers", headers=headers).json()}
    assert consumers["outbox"]["through_sequence"] == 4 and consumers["outbox"]["behind"] == 0
    assert client.get("/api/v1/decision-batches?unacknowledged_only=true", headers=headers).json() == []


def test_the_schedule(db_session, tenant_factory, monkeypatch):
    tenant, account, users = tenant_factory.make(slug="outbox-schedule")
    other, other_account, other_users = tenant_factory.make(slug="outbox-schedule-off")
    decision_outbox_service.save(db_session, tenant.id, enabled=True, layout="openclear_csv", interval_minutes=30, settings={})
    _decide(db_session, tenant, users, _make_exception(db_session, tenant, account, users, "S1", "1.00", "2.00"), DecisionOutcome.PAY)
    _decide(db_session, other, other_users, _make_exception(db_session, other, other_account, other_users, "S1", "1.00", "2.00"), DecisionOutcome.PAY)
    db_session.commit()

    from tests.conftest import TEST_DATABASE_URL  # noqa: F401

    assert decision_outbox_service.run_due(db_session) == 1  # the other bank's outbox is off
    _decide(db_session, tenant, users, _make_exception(db_session, tenant, account, users, "S2", "1.00", "2.00"), DecisionOutcome.PAY)
    assert decision_outbox_service.run_due(db_session) == 0  # not due yet
    later = datetime.now(timezone.utc) + timedelta(minutes=31)
    assert decision_outbox_service.run_due(db_session, now=later) == 1


def test_settings_are_checked(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="outbox-settings")
    with pytest.raises(decision_outbox_service.OutboxError, match="required"):
        decision_outbox_service.save(db_session, tenant.id, enabled=True, layout="nacha_returns", interval_minutes=60, settings={})
    with pytest.raises(decision_outbox_service.OutboxError, match="every 5 minutes"):
        decision_outbox_service.save(db_session, tenant.id, enabled=True, layout="openclear_csv", interval_minutes=1, settings={})
    with pytest.raises(decision_outbox_service.OutboxError, match="Unknown decision layout"):
        decision_outbox_service.save(db_session, tenant.id, enabled=True, layout="someone_elses_layout", interval_minutes=60, settings={})


def test_a_nacha_return_file(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="outbox-nacha")
    decision_outbox_service.save(db_session, tenant.id, enabled=True, layout="nacha_returns", interval_minutes=60, settings=NACHA)
    _ach_return(db_session, tenant, account, users, "091000010000001", "75.00", "R01")
    _ach_return(db_session, tenant, account, users, "091000010000002", "12.34", "R10")
    _decide(db_session, tenant, users, _make_exception(db_session, tenant, account, users, "C1", "1.00", "2.00"), DecisionOutcome.RETURN)

    batch = decision_outbox_service.run(db_session, tenant.id)
    db_session.commit()
    content = client.get(f"/api/v1/decision-batches/{batch.id}/file",
                         headers=login_headers(client, tenant.slug, users["admin"].email)).content.decode()
    records = content.splitlines()
    assert all(len(r) == 94 for r in records) and len(records) % 10 == 0
    entries = [r for r in records if r.startswith("6")]
    addenda = [r for r in records if r.startswith("799")]
    assert [e[1:3] for e in entries] == ["26", "26"]  # returns of checking debits; the check is skipped
    assert [a[3:6] for a in addenda] == ["R01", "R10"] and addenda[0][6:21] == "091000010000001"
    assert records[0][3:13] == " 091000080" and records[0][13:23] == " 231380104"  # to the operator, from the bank

    try:
        created = httpx.post(f"{MOOV}/files/create", content=content.encode(), headers={"Content-Type": "text/plain"}, timeout=5)
    except httpx.HTTPError:
        pytest.skip("Moov ACH isn't running (docker run -p 18080:8080 moov/ach)")
    assert created.status_code == 200 and created.json()["error"] is None, created.text[:500]
    assert httpx.get(f"{MOOV}/files/{created.json()['id']}/validate", timeout=5).json()["error"] is None


def test_a_layout_missing_settings_is_reported(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="outbox-nacha-unset")
    _ach_return(db_session, tenant, account, users, "091000010000003")
    response = client.get("/api/v1/decisions/file?layout=nacha_returns", headers=login_headers(client, tenant.slug, users["admin"].email))
    assert response.status_code == 422 and "routing number" in response.json()["detail"]
