# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date
from decimal import Decimal

from openclear.domain.ach_authorization_rule import AchAuthorizationRule
from openclear.domain.ach_transaction import AchMatchStatus, AchTransactionType
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.ach_authorization_repo import AchAuthorizationRepository
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import issued_item_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def _make_ach_exception(db_session, tenant, account, trace="ALLOW-TRACE-1"):
    txn = ingest_ach_transaction(
        db_session, tenant.id,
        AchTransactionSubmission(
            account_id=account.id, originator_id="ACMECORP", originator_name="Acme Corp", receiver_id="RCV-1",
            amount=Decimal("250.00"), transaction_type=AchTransactionType.DEBIT, sec_code="WEB",
            trace_number=trace, effective_date=date(2026, 1, 10),
        ),
    )
    db_session.commit()
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=txn.id)[0]
    return txn, exception


def _make_check_exception(db_session, tenant, account, users, check_number="ALLOW-CHK-1"):
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number=check_number, amount=Decimal("100.00"), payee_name="X", issue_date=date(2026, 1, 1)
        ),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()
    paid_item = ingest_paid_item(
        db_session, tenant.id,
        PaidItemSubmission(account_id=account.id, check_number=check_number, presented_amount=Decimal("999.00"), presented_date=date(2026, 1, 10)),
    )
    db_session.commit()
    return ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)[0]


def test_exception_detail_shows_allow_list_link_for_ach_only(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="allow-link-ach-only")
    _txn, ach_exception = _make_ach_exception(db_session, tenant, account)
    check_exception = _make_check_exception(db_session, tenant, account, users)
    _login(client, tenant.slug, users["admin"].email)

    ach_page = client.get(f"/ui/exceptions/{ach_exception.id}")
    assert f"/ui/ach/authorizations/new?from_exception={ach_exception.id}" in ach_page.text

    check_page = client.get(f"/ui/exceptions/{check_exception.id}")
    assert "/ui/ach/authorizations/new?from_exception=" not in check_page.text


def test_new_authorization_form_prefills_from_ach_exception(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="allow-prefill-ach")
    _txn, exception = _make_ach_exception(db_session, tenant, account)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/ach/authorizations/new?from_exception={exception.id}")
    assert resp.status_code == 200
    assert 'value="ACMECORP"' in resp.text
    assert 'value="Acme Corp"' in resp.text
    assert 'value="RCV-1"' in resp.text
    assert 'value="WEB"' in resp.text
    assert f'value="{exception.id}"' in resp.text


def test_new_authorization_form_ignores_check_exception(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="allow-prefill-wrong-network")
    exception = _make_check_exception(db_session, tenant, account, users)
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/ach/authorizations/new?from_exception={exception.id}")
    assert resp.status_code == 200
    assert 'name="originator_id" value=""' in resp.text


def test_new_authorization_form_ignores_unknown_exception_id(client, db_session, tenant_factory):
    import uuid

    tenant, _account, users = tenant_factory.make(slug="allow-prefill-unknown")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/ach/authorizations/new?from_exception={uuid.uuid4()}")
    assert resp.status_code == 200
    assert 'name="originator_id" value=""' in resp.text


def test_submit_prefilled_form_creates_rule_and_audits_source_exception(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="allow-submit-audit")
    _txn, exception = _make_ach_exception(db_session, tenant, account)
    csrf = _login(client, tenant.slug, users["admin"].email)
    client.get(f"/ui/ach/authorizations/new?from_exception={exception.id}")

    resp = client.post(
        "/ui/ach/authorizations",
        data={
            "csrf_token": csrf, "from_exception_id": str(exception.id), "account_id": str(account.id),
            "originator_id": "ACMECORP", "originator_name": "Acme Corp", "receiver_id": "RCV-1",
            "allowed_sec_codes": "WEB", "effective_date": "2026-01-15",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.expire_all()
    rules = AchAuthorizationRepository(db_session, tenant.id).list(originator_id="ACMECORP")
    assert len(rules) == 1
    assert rules[0].receiver_id == "RCV-1"

    from openclear.domain.audit_log_entry import AuditLogEntry

    entry = db_session.query(AuditLogEntry).filter(AuditLogEntry.resource_id == rules[0].id).one()
    assert str(exception.id) in entry.summary


def test_clearing_receiver_id_creates_blanket_rule(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="allow-submit-blanket")
    _txn, exception = _make_ach_exception(db_session, tenant, account)
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/ach/authorizations",
        data={
            "csrf_token": csrf, "from_exception_id": str(exception.id), "account_id": str(account.id),
            "originator_id": "ACMECORP", "originator_name": "Acme Corp", "receiver_id": "",
            "allowed_sec_codes": "WEB", "effective_date": "2026-01-15",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    db_session.expire_all()
    rules = AchAuthorizationRepository(db_session, tenant.id).list(originator_id="ACMECORP")
    assert len(rules) == 1
    assert rules[0].receiver_id is None


def test_allow_rule_suppresses_repeat_of_the_same_pattern(client, db_session, tenant_factory):
    """End-to-end: an ACH transaction that raised an exception once should clear cleanly
    the second time, after its originator+receiver were added to the allow list."""
    tenant, account, users = tenant_factory.make(slug="allow-suppresses-repeat")
    _txn, exception = _make_ach_exception(db_session, tenant, account, trace="ALLOW-TRACE-FIRST")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/ach/authorizations",
        data={
            "csrf_token": csrf, "from_exception_id": str(exception.id), "account_id": str(account.id),
            "originator_id": "ACMECORP", "originator_name": "Acme Corp", "receiver_id": "RCV-1",
            "allowed_sec_codes": "WEB", "effective_date": "2026-01-01",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    second_txn = ingest_ach_transaction(
        db_session, tenant.id,
        AchTransactionSubmission(
            account_id=account.id, originator_id="ACMECORP", originator_name="Acme Corp", receiver_id="RCV-1",
            amount=Decimal("250.00"), transaction_type=AchTransactionType.DEBIT, sec_code="WEB",
            trace_number="ALLOW-TRACE-SECOND", effective_date=date(2026, 1, 20),
        ),
    )
    db_session.commit()

    assert second_txn.match_status == AchMatchStatus.MATCHED
