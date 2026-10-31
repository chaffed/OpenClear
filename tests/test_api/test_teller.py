# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Teller positive pay (TELLER_PLAN.md TL-1, TL-2): a teller platform verifies a check before
cash is paid at a branch, by the clearing rules, and records it cashed once, so the same check
presented later is a duplicate. Only a client with check:verify can, and it can do nothing else."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from openclear.domain.account import CheckReviewMode
from openclear.domain.teller_verification import TellerVerification
from openclear.services import api_client_service, issued_item_service, security_group_service, stop_payment_service
from tests.conftest import login_headers


def _teller(client, db_session, tenant) -> dict:
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Teller platform")
    issued = api_client_service.create_client(db_session, tenant.id, name="Branch teller system", security_group_id=group.id)
    db_session.commit()
    token = client.post("/api/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": issued.client.client_id,
                                                     "client_secret": issued.secret})
    assert token.status_code == 200, token.text
    return {"Authorization": f"Bearer {token.json()['access_token']}"}


def _issue(db_session, tenant, account, number, amount="250.00", payee="Acme Supply Co", issued_on=None):
    item = issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number=number, amount=Decimal(amount), payee_name=payee,
        issue_date=issued_on or date.today() - timedelta(days=3)), submitted_by_user_id=None)
    db_session.commit()
    return item


def _verify(client, headers, account, number, amount="250.00", **extra):
    """As a teller sends it: with the payee written on the check, unless a test leaves it out."""
    body = {"account_number": account.account_number, "check_number": number, "amount": amount, "payee_name": "Acme Supply Co", **extra}
    resp = client.post("/api/v1/checks/verify", headers=headers, json={k: v for k, v in body.items() if v is not None})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _cash(client, headers, verification, amount="250.00"):
    return client.post("/api/v1/checks/cashed", headers=headers, json={"verification_id": verification["verification_id"], "amount": amount})


@pytest.fixture
def bank(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="teller-bank")
    return tenant, account, users, _teller(client, db_session, tenant)


def test_every_answer_is_what_clearing_would_decide(client, db_session, bank):
    tenant, account, _users, headers = bank
    _issue(db_session, tenant, account, "100")
    _issue(db_session, tenant, account, "101")
    voided = _issue(db_session, tenant, account, "102")
    issued_item_service.void_issued_item(db_session, tenant.id, voided.id, reason="Spoiled")
    _issue(db_session, tenant, account, "103")
    stop_payment_service.create_stop_payment(db_session, tenant.id, stop_payment_service.StopPaymentInput(
        account_id=account.id, check_number="103", amount=None, effective_date=date.today() - timedelta(days=1), expiration_date=None,
        reason="Lost"), created_by_user_id=None)
    _issue(db_session, tenant, account, "104", issued_on=date.today() - timedelta(days=400))
    for number in ("105", "106", "107"):
        _issue(db_session, tenant, account, number)  # a match is good for one teller at a time, so each case has its own check
    db_session.commit()

    cases = [
        ("100", "250.00", {}, "match", []),
        ("101", "275.00", {}, "amount_differs", ["amount_differs"]),
        ("105", "250.00", {"payee_name": "Somebody Else Entirely"}, "payee_differs", ["payee_differs"]),
        ("106", "250.00", {"payee_name": "ACME SUPPLY"}, "match", []),  # read loosely, as clearing compares OCR's
        ("107", "250.00", {"payee_name": None}, "payee_needed", ["payee_needed"]),  # the customer gave a payee: it must be checked
        ("102", "250.00", {}, "voided", ["voided"]),
        ("103", "250.00", {}, "stopped", ["stopped"]),
        ("104", "250.00", {}, "stale", ["stale"]),
        ("999", "10.00", {}, "not_issued", ["not_issued"]),
    ]
    for number, amount, extra, result, problems in cases:
        answer = _verify(client, headers, account, number, amount, **extra)
        assert (number, answer["result"], answer["problems"]) == (number, result, problems)
        # What was issued, only to explain a decline, and never the payee a teller didn't send.
        assert (answer["issued"] is None) == (result in ("match", "not_issued", "payee_needed"))
    unknown = client.post("/api/v1/checks/verify", headers=headers, json={"account_number": "NOPE-1", "check_number": "1", "amount": "1.00"})
    assert unknown.json()["result"] == "not_on_positive_pay"


def test_cashing_once_marks_it_paid_and_clearing_later_finds_a_duplicate(client, db_session, bank):
    from openclear.domain.exception_item import ExceptionItem
    from openclear.domain.issued_item import IssuedItem
    from openclear.domain.paid_item import PaidItemSource
    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item

    tenant, account, _users, headers = bank
    issued = _issue(db_session, tenant, account, "200")
    answer = _verify(client, headers, account, "200", teller_id="T-17", branch="Main St")
    cashed = _cash(client, headers, answer)
    assert cashed.status_code == 201 and cashed.json()["exception_types"] == []
    db_session.expire_all()
    assert db_session.get(IssuedItem, issued.id).status.value == "paid"
    again = _cash(client, headers, answer)  # the platform lost the first answer: it's told what was recorded (REVIEW_1_33_PLAN.md T10)
    assert again.status_code == 409 and again.json()["detail"]["message"] == "That verification was already recorded as cashed."
    assert again.json()["detail"]["paid_item_id"] == cashed.json()["paid_item_id"] and again.json()["detail"]["cashed_at"]
    assert _verify(client, headers, account, "200")["result"] == "already_paid"  # a second branch is told

    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(
        account_id=account.id, check_number="200", presented_amount=Decimal("250.00"), presented_date=date.today(),
        source=PaidItemSource.API))
    db_session.commit()
    item = db_session.execute(select(ExceptionItem).where(ExceptionItem.source_item_id == paid.id)).scalar_one()
    assert "duplicate_paid" in item.exception_types


def test_a_decline_an_expired_verification_or_another_amount_cant_be_cashed(client, db_session, bank):
    tenant, account, _users, headers = bank
    _issue(db_session, tenant, account, "300")
    declined = _verify(client, headers, account, "300", "999.00")
    assert _cash(client, headers, declined, "999.00").status_code == 409
    answer = _verify(client, headers, account, "300")
    assert "isn't the amount verified" in _cash(client, headers, answer, "260.00").json()["detail"]
    verification = db_session.get(TellerVerification, uuid.UUID(answer["verification_id"]))
    verification.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert "expired" in _cash(client, headers, answer).json()["detail"]
    other = client.post("/api/v1/checks/cashed", headers=headers,
                        json={"verification_id": "00000000-0000-0000-0000-000000000000", "amount": "1.00"})
    assert other.status_code == 404


def test_a_reverse_account_is_the_customers_to_review(client, db_session, bank):
    tenant, account, _users, headers = bank
    account.check_review_mode = CheckReviewMode.REVERSE
    db_session.commit()
    assert _verify(client, headers, account, "400", "12.00")["result"] == "customer_review"


def test_the_teller_platform_can_do_nothing_else_and_people_cant_verify(client, db_session, bank):
    tenant, account, users, headers = bank
    assert client.get("/api/v1/issued-items", headers=headers).status_code == 403  # can't download the check register
    assert client.get("/api/v1/paid-items", headers=headers).status_code == 403
    admin = login_headers(client, tenant.slug, users["admin"].email)
    body = {"account_number": account.account_number, "check_number": "1", "amount": "1.00"}
    assert client.post("/api/v1/checks/verify", headers=admin, json=body).status_code == 403  # not even the bank's admin, by default


def test_every_call_is_recorded_and_declines_reach_the_customer(client, db_session, bank):
    from openclear.domain.audit_log_entry import AuditLogEntry
    from openclear.domain.security_event import SecurityEvent
    from openclear.services import customer_service
    from tests.test_web.test_layouts_and_fixes import _login

    tenant, account, users, headers = bank
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="T-1", name="Teller Co"))
    account.customer_id = customer.id
    db_session.commit()
    _issue(db_session, tenant, account, "500")
    _verify(client, headers, account, "500", teller_id="T-9", branch="Elm")
    _verify(client, headers, account, "501", "40.00", teller_id="T-9", branch="Elm")
    actions = [e.action for e in db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id,
                                                                                AuditLogEntry.action.like("teller.%"))).scalars()]
    assert sorted(actions) == ["teller.verify", "teller.verify.declined"]
    names = {e.name for e in db_session.execute(select(SecurityEvent).where(SecurityEvent.tenant_id == tenant.id)).scalars()}
    assert {"teller.verify", "teller.verify.declined"} <= names

    _login(client, tenant.slug, users["admin"].email)
    page = client.get(f"/ui/exceptions?customer={customer.id}").text
    assert "1 check was refused at a branch in the last 7 days" in page and "Check 501 for 40.00" in page and "not issued" in page


@pytest.mark.parametrize("issued", [False, True])
def test_many_declines_of_any_kind_look_like_probing(client, db_session, bank, issued):
    """Trying check numbers, or trying amounts against issued checks: either reads the register
    (REVIEW_1_33_PLAN.md T3), so either crosses the line."""
    from openclear.domain.security_event import SecurityEvent
    from openclear.services import teller_service

    tenant, account, _users, headers = bank
    for n in range(teller_service.PROBE_THRESHOLD):
        if issued:
            _issue(db_session, tenant, account, f"9{n:03d}")
        _verify(client, headers, account, f"9{n:03d}", "5.00", teller_id="T-404")
    events = db_session.execute(select(SecurityEvent).where(SecurityEvent.name == "teller.verify.probing",
                                                            SecurityEvent.tenant_id == tenant.id)).scalars().all()
    assert len(events) == 1 and events[0].severity == "high"


def test_a_decline_says_nothing_the_teller_didnt_send(client, db_session, bank):
    tenant, account, _users, headers = bank
    _issue(db_session, tenant, account, "950")
    needed = _verify(client, headers, account, "950", payee_name=None)
    assert needed["result"] == "payee_needed" and needed["issued"] is None  # not the payee it's asking for (T2)
    amount = _verify(client, headers, account, "950", "1.00")
    assert amount["result"] == "amount_differs" and amount["issued"]["amount"] == "250.00" and amount["issued"]["payee_name"] is None
    payee = _verify(client, headers, account, "950", payee_name="Somebody Else")
    assert payee["result"] == "payee_differs" and payee["issued"]["payee_name"] == "Acme Supply Co"


def test_a_check_in_another_tellers_hands_is_declined(client, db_session, bank):
    tenant, account, _users, headers = bank
    _issue(db_session, tenant, account, "960")
    first = _verify(client, headers, account, "960", teller_id="T-1", branch="Main St")
    second = _verify(client, headers, account, "960", teller_id="T-2", branch="Elm")
    assert first["result"] == "match" and second["result"] == "being_cashed"  # two branches can't both pay out (T5)
    verification = db_session.get(TellerVerification, uuid.UUID(first["verification_id"]))
    verification.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)  # the first teller never cashed it
    db_session.commit()
    third = _verify(client, headers, account, "960", teller_id="T-2", branch="Elm")
    assert third["result"] == "match"
    assert _cash(client, headers, third).status_code == 201
    assert _verify(client, headers, account, "960")["result"] == "already_paid"


def test_verify_reads_the_issued_item_and_its_stops_once(client, db_session, bank, engine):
    tenant, account, _users, headers = bank
    _issue(db_session, tenant, account, "600")
    reads = []

    def count(_conn, _cursor, statement, *_args):
        lowered = statement.lower()
        if lowered.startswith("select") and ("from issued_item" in lowered or "from stop_payment" in lowered):
            reads.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        _verify(client, headers, account, "600")
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(reads) == 2, reads  # a later change can't quietly make it slower


def test_a_customer_no_longer_enrolled_is_cashed_by_the_usual_procedure(client, db_session, bank):
    """The product owner (2026-10-22): enrolled in positive pay or reverse positive pay, a check
    not found is declined; not enrolled, the teller cashes it by standard operating procedure."""
    from openclear.services import customer_service

    tenant, account, _users, headers = bank
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="E-1", name="Gone Co"))
    account.customer_id = customer.id
    db_session.commit()
    assert _verify(client, headers, account, "700", "30.00")["result"] == "not_issued"  # enrolled: declined
    customer.is_active = False
    db_session.commit()
    assert _verify(client, headers, account, "700", "30.00")["result"] == "not_on_positive_pay"  # not enrolled: the usual procedure
    customer.is_active, customer.service_ended_at = True, datetime.now(timezone.utc)  # offboarded (REVIEW_1_33_PLAN.md T4)
    db_session.commit()
    assert _verify(client, headers, account, "700", "30.00")["result"] == "not_on_positive_pay"


def test_no_payee_is_needed_when_the_customer_gave_none(client, db_session, bank):
    tenant, account, _users, headers = bank
    _issue(db_session, tenant, account, "800", payee="")
    assert _verify(client, headers, account, "800", payee_name=None)["result"] == "match"

