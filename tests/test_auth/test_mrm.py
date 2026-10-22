# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Model risk management (COMPLIANCE_PLAN.md C2; services/mrm_service.py): replaying
historical files through today's rules in a dry run, and the synthetic anomaly-capture
test. Above all, that a dry run leaves nothing behind."""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from openclear.domain.ach_transaction import AchTransaction
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.issued_item import IssuedItem
from openclear.domain.job import Job
from openclear.domain.paid_item import PaidItem
from openclear.networks.ach.bulk_import import ingest_nacha_entries
from openclear.bulk_import.nacha import parse_nacha_file
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.services import ach_authorization_service, issued_item_service, mrm_service, plugin_service
from tests.conftest import login_headers
from tests.test_auth.test_plugins_bank_choices import example_plugin  # noqa: F401 -- fixture
from tests.x937_builder import Check, build


def _counts(db_session):
    return {model.__name__: db_session.execute(select(func.count()).select_from(model)).scalar()
            for model in (PaidItem, AchTransaction, ExceptionItem, IssuedItem, Job)}


def _issue(db_session, tenant, account, users, number, amount):
    issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number=number, amount=Decimal(amount), payee_name="Vendor", issue_date=date(2026, 1, 2),
    ), submitted_by_user_id=users["preparer"].id)


def _check_history(db_session, tenant, account, users):
    """Three checks presented in January: two clean, one that was flagged and returned."""
    from openclear.domain.paid_item import PaidItemSettlementStatus

    _issue(db_session, tenant, account, users, "7001", "20000.00")
    _issue(db_session, tenant, account, users, "7002", "45.00")
    _issue(db_session, tenant, account, users, "7003", "300.00")
    for number, amount in (("7001", "20000.00"), ("7002", "45.00"), ("7003", "3000.00")):
        paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(
            account_id=account.id, check_number=number, presented_amount=Decimal(amount), presented_date=date(2026, 1, 15)))
        if number == "7003":
            paid.settlement_status = PaidItemSettlementStatus.RETURNED
    db_session.commit()
    on_us = f"{account.account_number}/"
    return build([[[Check(on_us=on_us, aux_on_us="7001", amount_cents=2_000_000), Check(on_us=on_us, aux_on_us="7002", amount_cents=4500),
                    Check(on_us=on_us, aux_on_us="7003", amount_cents=300_000), Check(on_us="99999/", aux_on_us="1", amount_cents=100)]]])


def test_replaying_a_cash_letter_compares_today_with_history_and_keeps_nothing(example_plugin, db_session, tenant_factory):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="mrm-check")
    letter = _check_history(db_session, tenant, account, users)

    # Unchanged rules: everything that was seen agrees with history.
    before = _counts(db_session)
    same = mrm_service.replay(db_session, tenant.id, filename="jan.x937", content=letter, user_id=users["admin"].id)
    db_session.commit()
    assert _counts(db_session) == before  # the dry run left nothing behind
    report = same.report
    assert report["network"] == "check" and report["items"] == 4 and report["compared_with_history"] == 3
    assert report["match_rate"] == 1.0 and report["false_positive_rate"] == 0.0 and report["missed_return_rate"] == 0.0
    assert report["unhandled_rate"] == 0.25 and report["variances"]["unhandled"][0]["item"].startswith("item 4")
    # The originals are as they were (the rewind was inside the dry run too).
    statuses = {p.check_number: p.settlement_status.value for p in db_session.execute(select(PaidItem)).scalars()}
    assert statuses == {"7001": "paid", "7002": "paid", "7003": "returned"}

    # A proposed rule (the example plugin's large round amounts): the $20,000 check it
    # would now flag was paid, so it's a false positive.
    plugin_service.set_enabled(db_session, tenant.id, "matching_rules", "example_large_round_amount", True, user_id=None)
    db_session.commit()
    proposed = mrm_service.replay(db_session, tenant.id, filename="jan.x937", content=letter, user_id=users["admin"].id).report
    assert proposed["false_positive_rate"] == 0.5 and proposed["match_rate"] == round(2 / 3, 4)
    assert [v["item"].split(" ")[1] for v in proposed["variances"]["newly_flagged"]] == ["1"]
    assert proposed["exception_types"]["large_round_amount"] == 1


def test_replaying_a_nacha_file(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="mrm-ach")
    ach_authorization_service.create_ach_authorization(db_session, tenant.id, ach_authorization_service.AchAuthorizationInput(
        account_id=account.id, originator_id="9SYNTHETIC", originator_name="SYNTHETIC CO", receiver_id=None, max_amount=Decimal("500.00"),
        frequency_limit=None, allowed_sec_codes=None, effective_date=date(2026, 1, 1), expiration_date=None,
    ), created_by_user_id=users["preparer"].id)
    file = mrm_service.build_nacha([
        {"transaction_code": "27", "account": account.account_number, "amount_cents": 12000, "individual_id": "A", "trace": "123456780000001"},
        {"transaction_code": "27", "account": account.account_number, "amount_cents": 90000, "individual_id": "B", "trace": "123456780000002"},
    ])
    ingest_nacha_entries(db_session, tenant.id, parse_nacha_file(file))
    db_session.commit()
    before = _counts(db_session)

    response = client.post("/api/v1/mrm/replay", headers=login_headers(client, tenant.slug, users["admin"].email),
                           files={"file": ("jan.ach", file, "text/plain")})
    assert response.status_code == 201, response.text
    report = response.json()["report"]
    assert report["network"] == "ach" and report["compared_with_history"] == 2 and report["match_rate"] == 1.0
    assert report["exception_types"] == {"amount_exceeds_limit": 1}
    db_session.expire_all()
    assert _counts(db_session) == before
    assert sorted(t.trace_number for t in db_session.execute(select(AchTransaction)).scalars()) == ["123456780000001", "123456780000002"]

    # Only for those who manage models, and a file that can't be read is refused.
    viewer = login_headers(client, tenant.slug, users["approver"].email)
    assert client.post("/api/v1/mrm/replay", headers=viewer, files={"file": ("x", file)}).status_code == 403
    admin = login_headers(client, tenant.slug, users["admin"].email)
    assert client.post("/api/v1/mrm/replay", headers=admin, files={"file": ("x", b"\x00\x00\x00\x09garbage")}).status_code == 422
    runs = client.get("/api/v1/mrm/runs?kind=mrm_replay", headers=admin).json()
    assert len(runs) == 1 and runs[0]["report"]["file"] == "jan.ach"


def test_anomaly_capture_catches_every_vector_and_keeps_nothing(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="mrm-anomaly")
    before = _counts(db_session)
    response = client.post("/api/v1/mrm/anomaly-capture", headers=login_headers(client, tenant.slug, users["admin"].email))
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["passed"] is True, run["report"]
    keys = {p["key"] for p in run["report"]["probes"]}
    assert {"check_duplicate_serial", "check_altered_amount", "check_wrong_account", "ach_altered_amount", "ach_unknown_originator",
            "x937_bad_amount", "nacha_bad_amount", "check_clean", "ach_clean"} <= keys
    db_session.expire_all()
    assert _counts(db_session) == before
    from openclear.domain.account import Account

    assert not db_session.execute(select(Account).where(Account.account_number.like("SY%"))).scalars().all()


def test_anomaly_capture_fails_when_a_rule_stops_catching(db_session, tenant_factory, monkeypatch):
    """If a vector stops being caught (here, the duplicate rule is broken: it never reports a
    duplicate, however it was found), the run fails and says which."""
    from openclear.networks.check import adapter
    from openclear.networks.check.types import CheckExceptionType

    real_rules = adapter.evaluate_check_rules

    def without_duplicates(inputs):
        found, related = real_rules(inputs)
        return [t for t in found if t != CheckExceptionType.DUPLICATE_PAID], related

    tenant, _account, users = tenant_factory.make(slug="mrm-broken")
    monkeypatch.setattr(adapter, "evaluate_check_rules", without_duplicates)
    run = mrm_service.anomaly_capture(db_session, tenant.id, user_id=None)
    db_session.commit()
    assert run.passed is False and "check_duplicate_serial" in run.summary
    assert mrm_service.latest(db_session, tenant.id, mrm_service.ANOMALY_CAPTURE).id == run.id


def test_the_daily_job_runs_for_every_bank(db_session, tenant_factory, session_factory, monkeypatch):
    from openclear.workers import tasks

    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)

    first, _a, _u = tenant_factory.make(slug="mrm-daily-a")
    second, _b, _v = tenant_factory.make(slug="mrm-daily-b")
    tasks.anomaly_capture_job()
    db_session.expire_all()
    for tenant in (first, second):
        run = mrm_service.latest(db_session, tenant.id, mrm_service.ANOMALY_CAPTURE)
        assert run is not None and run.passed and run.requested_by_user_id is None


def test_the_model_risk_page(client, db_session, tenant_factory):
    from tests.conftest import TenantFactory

    tenant, account, users = tenant_factory.make(slug="mrm-web")
    letter = _check_history(db_session, tenant, account, users)
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    assert "hasn't run yet" in client.get("/ui/admin/model-risk").text
    replayed = client.post("/ui/admin/model-risk/replay", data={"csrf_token": csrf}, files={"file": ("jan.x937", letter)})
    assert replayed.status_code == 200 and "Agree with history" in replayed.text and "100.0%" in replayed.text
    captured = client.post("/ui/admin/model-risk/anomaly-capture", data={"csrf_token": csrf})
    assert "Last anomaly capture: <strong>Passed</strong>" in captured.text and "Missed" not in captured.text
    refused = client.post("/ui/admin/model-risk/replay", data={"csrf_token": csrf}, files={"file": ("bad.x937", b"\x00\x00\x00\x09nonsense")})
    assert "couldn" in refused.text
