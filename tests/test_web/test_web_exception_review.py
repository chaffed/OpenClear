# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The exception review screens (FIX_PLAN.md Phase 6): the evidence panel, fraud-risk
wording, safe decision forms, the queue's defaults, and the approvals follow-ups."""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from openclear.db.tenancy import TenantContext
from openclear.domain.ach_transaction import AchTransactionType
from openclear.domain.customer_disposition_setting import DispositionMode
from openclear.domain.decision import DecisionOutcome
from openclear.domain.exception_item import ExceptionStatus
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import ach_authorization_service, auto_disposition_service, decision_service, stop_payment_service
from tests.conftest import TenantFactory
from tests.test_ml.test_per_customer_ml import _make_customer_with_account, _make_exception


def _login(client, tenant, email):
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token")})


def _ctx(tenant, user):
    return TenantContext(
        tenant_id=tenant.id, user_id=user.id, security_group_id=uuid.uuid4(), permissions=frozenset(),
        tenant_slug=tenant.slug, tenant_name=tenant.name, accent_color=None, has_logo=False, has_favicon=False,
        customer_id=None, customer_name=None,
    )


def _ach_exception(db_session, tenant, account, amount="250.00", trace="REV-1"):
    txn = ingest_ach_transaction(
        db_session, tenant.id,
        AchTransactionSubmission(
            account_id=account.id, originator_id="ACMECORP", originator_name="Acme Corp", receiver_id="RCV-1",
            amount=Decimal(amount), transaction_type=AchTransactionType.DEBIT, sec_code="WEB",
            trace_number=trace, effective_date=date(2026, 1, 10),
        ),
    )
    db_session.commit()
    return ExceptionRepository(db_session, tenant.id).list(source_item_id=txn.id)[0]


# --- Evidence ---


def test_check_evidence_puts_issued_and_presented_side_by_side(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="rev-check")
    exception = _make_exception(db_session, tenant, account, users, "5001", "100.00", "999.00")
    _login(client, tenant, users["admin"].email)

    page = client.get(f"/ui/exceptions/{exception.id}").text

    assert "What was expected vs. what was presented" in page
    amount_row = re.search(r'<tr class="mismatch">\s*<th scope="row">Amount(.*?)</tr>', page, re.S)
    assert amount_row, "the amount row should be flagged"
    assert "$100.00" in amount_row.group(1) and "$999.00" in amount_row.group(1)
    assert "Doesn&#39;t match" in amount_row.group(1) or "Doesn't match" in amount_row.group(1)
    assert "Some Vendor" in page  # the issued payee
    assert account.account_number in page


def test_check_not_in_file_explains_there_is_nothing_to_compare(client, db_session, tenant_factory):
    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item

    tenant, account, users = tenant_factory.make(slug="rev-nif")
    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(
        account_id=account.id, check_number="NOPE-1", presented_amount=Decimal("42.00"), presented_date=date(2026, 1, 10)))
    db_session.commit()
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid.id)[0]
    _login(client, tenant, users["admin"].email)

    page = client.get(f"/ui/exceptions/{exception.id}").text

    assert "Not in issued file" in page
    assert "No issued check with this number is on file" in page


def test_stop_payment_is_explained(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="rev-stop")
    stop_payment_service.create_stop_payment(
        db_session, tenant.id,
        stop_payment_service.StopPaymentInput(account_id=account.id, check_number="5002", amount=None,
                                              effective_date=date(2026, 1, 2), expiration_date=None, reason="Lost in mail"),
        created_by_user_id=users["admin"].id,
    )
    db_session.commit()
    exception = _make_exception(db_session, tenant, account, users, "5002", "100.00", "100.00")
    _login(client, tenant, users["admin"].email)

    page = client.get(f"/ui/exceptions/{exception.id}").text

    assert "Stop payment" in page
    assert "A stop payment is in effect from 2026-01-02 (Lost in mail)" in page


def test_ach_evidence_shows_the_authorized_limit(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="rev-ach")
    ach_authorization_service.create_ach_authorization(
        db_session, tenant.id,
        ach_authorization_service.AchAuthorizationInput(
            account_id=account.id, originator_id="ACMECORP", originator_name="Acme Corp", receiver_id=None,
            max_amount=Decimal("100.00"), frequency_limit=None, allowed_sec_codes=["WEB"],
            effective_date=date(2026, 1, 1), expiration_date=None),
        created_by_user_id=users["admin"].id,
    )
    db_session.commit()
    exception = _ach_exception(db_session, tenant, account, amount="250.00")
    _login(client, tenant, users["admin"].email)

    page = client.get(f"/ui/exceptions/{exception.id}").text

    assert "Over authorized amount" in page
    amount_row = re.search(r'<tr class="mismatch">\s*<th scope="row">Amount(.*?)</tr>', page, re.S)
    assert amount_row and "$100.00" in amount_row.group(1) and "$250.00" in amount_row.group(1)


def test_unauthorized_originator_is_explained(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="rev-ach-unauth")
    exception = _ach_exception(db_session, tenant, account)
    _login(client, tenant, users["admin"].email)

    page = client.get(f"/ui/exceptions/{exception.id}").text

    assert "Unauthorized originator" in page
    assert "No ACH authorization is on file for Acme Corp (ACMECORP)" in page


def test_deadline_says_what_happens_next(client, db_session, tenant_factory):
    tenant, _house, users = tenant_factory.make(slug="rev-deadline")
    customer, account = _make_customer_with_account(db_session, tenant, "DL-1")
    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "check", mode=DispositionMode.FIXED_RETURN, response_window_hours=4,
        default_ach_return_reason_id=None,
    )
    db_session.commit()
    exception = _make_exception(db_session, tenant, account, users, "5003", "100.00", "999.00")
    assert exception.decision_deadline is not None
    _login(client, tenant, users["admin"].email)

    page = client.get(f"/ui/exceptions/{exception.id}").text

    assert "Decide by" in page and "it will be returned automatically" in page
    assert "Customer DL-1" in page


# --- Fraud risk wording ---


def test_fraud_risk_is_shown_as_risk_not_a_raw_score(client, db_session, tenant_factory):
    """ml_score is the probability the item should be PAID; a low score is a high risk."""
    tenant, account, users = tenant_factory.make(slug="rev-risk")
    exception = _make_exception(db_session, tenant, account, users, "5004", "100.00", "999.00")
    exception.ml_score = 0.1
    db_session.commit()
    _login(client, tenant, users["admin"].email)

    page = client.get(f"/ui/exceptions/{exception.id}").text

    assert ">High<" in page and "90% estimated chance it should be returned" in page
    assert "0.100" not in page
    assert 'data-confirm-title="Pay a high-risk item?"' in page  # paying a High-risk item asks first


def test_template_helper_levels():
    from openclear.web.templates import _fraud_risk

    assert _fraud_risk(None) is None
    assert _fraud_risk(0.95)["level"] == "Low"
    assert _fraud_risk(0.7)["level"] == "Medium"
    assert _fraud_risk(0.3) == {"level": "High", "badge": "badge-danger", "return_percent": 70}


# --- Safe decision forms ---


def test_outcome_starts_unchosen_and_is_required(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="rev-choose")
    exception = _make_exception(db_session, tenant, account, users, "5005", "100.00", "999.00")
    _login(client, tenant, users["admin"].email)

    page = client.get(f"/ui/exceptions/{exception.id}").text
    # Two buttons, neither chosen for you; Return's reasons stay closed until asked for.
    assert 'name="outcome" value="pay"' in page and 'name="outcome" value="return"' in page
    assert '<details class="decision-return" open' not in page and "recommended-tag" not in page

    resp = client.post(f"/ui/exceptions/{exception.id}/decide",
                       data={"csrf_token": client.cookies.get("csrf_token"), "outcome": "", "reason_code": "x"},
                       follow_redirects=False)

    assert resp.status_code == 303 and "Choose+Pay+or+Return" in resp.headers["location"]
    db_session.expire_all()
    assert exception.status == ExceptionStatus.OPEN


def test_without_dual_control_a_decider_sees_only_decide(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="rev-single", require_dual_control=False)
    exception = _make_exception(db_session, tenant, account, users, "5006", "100.00", "999.00")
    _login(client, tenant, users["admin"].email)

    page = client.get(f"/ui/exceptions/{exception.id}").text

    assert f'action="/ui/exceptions/{exception.id}/decide"' in page
    assert f'action="/ui/exceptions/{exception.id}/recommend"' not in page


def test_under_dual_control_a_maker_still_recommends(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="rev-dual", require_dual_control=True)
    exception = _make_exception(db_session, tenant, account, users, "5007", "100.00", "999.00")
    _login(client, tenant, users["preparer"].email)

    page = client.get(f"/ui/exceptions/{exception.id}").text

    assert f'action="/ui/exceptions/{exception.id}/recommend"' in page


# --- The queue ---


def test_queue_defaults_to_items_needing_attention(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="rev-queue")
    open_one = _make_exception(db_session, tenant, account, users, "5101", "100.00", "999.00")
    decided = _make_exception(db_session, tenant, account, users, "5102", "100.00", "888.00")
    decision_service.decide(db_session, tenant.id, decided.id, _ctx(tenant, users["approver"]),
                            outcome=DecisionOutcome.PAY, reason_code="ok", notes=None)
    db_session.commit()
    _login(client, tenant, users["admin"].email)

    default = client.get("/ui/exceptions").text
    everything = client.get("/ui/exceptions?status=all").text

    assert str(open_one.id) in default and str(decided.id) not in default
    assert str(open_one.id) in everything and str(decided.id) in everything
    assert "Amount mismatch" in default and "amount_mismatch" not in default
    assert account.account_number in default
    assert "Needs review" in default


def test_empty_queue_points_to_all_exceptions(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="rev-queue-empty")
    _login(client, tenant, users["admin"].email)

    assert "Nothing needs attention right now" in client.get("/ui/exceptions").text


# --- Approvals follow-ups ---


def test_recommended_ach_return_reason_is_stored_and_preselected(client, db_session, tenant_factory):
    from openclear.services import ach_return_reason_service

    tenant, account, users = tenant_factory.make(slug="rev-ach-reason", require_dual_control=True)
    exception = _ach_exception(db_session, tenant, account, trace="REV-R1")
    reason = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)[0]

    result = decision_service.submit_recommendation(
        db_session, tenant.id, exception.id, _ctx(tenant, users["preparer"]),
        outcome=DecisionOutcome.RETURN, reason_code="", notes=None, ach_return_reason_id=reason.id,
    )
    db_session.commit()
    assert result.error is None
    assert exception.recommended_ach_return_reason_id == reason.id

    _login(client, tenant, users["approver"].email)
    page = client.get(f"/ui/exceptions/{exception.id}").text
    assert f'value="{reason.id}" required checked' in page


def test_approvals_nav_shows_how_many_are_waiting_on_someone_else(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="rev-badge", require_dual_control=True)
    exception = _make_exception(db_session, tenant, account, users, "5201", "100.00", "999.00")
    decision_service.submit_recommendation(
        db_session, tenant.id, exception.id, _ctx(tenant, users["preparer"]),
        outcome=DecisionOutcome.RETURN, reason_code="fraud", notes=None,
    )
    db_session.commit()

    _login(client, tenant, users["approver"].email)
    assert '<span class="nav-count" aria-label="1 waiting">1</span>' in client.get("/ui/exceptions").text


def test_approvals_badge_excludes_your_own_recommendations(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="rev-badge-own", require_dual_control=True)
    exception = _make_exception(db_session, tenant, account, users, "5202", "100.00", "999.00")
    decision_service.submit_recommendation(
        db_session, tenant.id, exception.id, _ctx(tenant, users["admin"]),
        outcome=DecisionOutcome.RETURN, reason_code="fraud", notes=None,
    )
    db_session.commit()

    _login(client, tenant, users["admin"].email)
    page = client.get("/ui/exceptions").text
    approvals_link = page.split('href="/ui/exceptions/approvals"')[1].split("</a>")[0]
    assert "nav-count" not in approvals_link  # not your own recommendation
    # (The Exceptions link does count it: it still needs a decision.)


def test_decision_deadline_helper_used_in_list(client, db_session, tenant_factory):
    tenant, _house, users = tenant_factory.make(slug="rev-deadline-list")
    customer, account = _make_customer_with_account(db_session, tenant, "DL-2")
    exception = _make_exception(db_session, tenant, account, users, "5301", "100.00", "999.00")
    exception.decision_deadline = datetime.now(timezone.utc) + timedelta(hours=3)
    db_session.commit()
    _login(client, tenant, users["admin"].email)

    page = client.get("/ui/exceptions").text

    assert ">Decide by</a></th>" in page and "data-relative-time" in page  # a sortable column
    assert "<th>Customer</th>" in page and "Customer DL-2" in page
