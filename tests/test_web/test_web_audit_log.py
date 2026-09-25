# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from pathlib import Path

from openclear.domain.audit_log_entry import AuditLogEntry
from tests.conftest import TenantFactory, login_headers


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_audit_log_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="audit-web-forbidden")
    # Preparer has every *:write permission but not audit_log:read (Admin-only by default)
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get("/ui/audit-log", follow_redirects=False)
    assert resp.status_code == 403


def test_issued_item_create_and_void_appear_in_audit_log(client, db_session, tenant_factory):
    from openclear.repositories.issued_item_repo import IssuedItemRepository

    tenant, account, users = tenant_factory.make(slug="audit-web-issued-item")
    csrf = _login(client, tenant.slug, users["admin"].email)

    client.post(
        "/ui/issued-items",
        data={
            "account_id": str(account.id),
            "check_number": "9101",
            "amount": "150.00",
            "payee_name": "Vendor A",
            "issue_date": "2026-01-01",
            "csrf_token": csrf,
        },
    )
    item = IssuedItemRepository(db_session, tenant.id).list(check_number="9101")[0]
    client.post(f"/ui/issued-items/{item.id}/void", data={"reason": "duplicate", "csrf_token": csrf})

    log_page = client.get("/ui/audit-log")
    assert "issued_item.create" in log_page.text
    assert "issued_item.void" in log_page.text
    assert "9101" in log_page.text
    assert users["admin"].email in log_page.text
    assert ">web<" in log_page.text


def test_web_and_api_channels_both_recorded(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="audit-web-api-channel")
    csrf = _login(client, tenant.slug, users["admin"].email)

    client.post(
        "/ui/issued-items",
        data={
            "account_id": str(account.id),
            "check_number": "9102",
            "amount": "50.00",
            "payee_name": "Vendor B",
            "issue_date": "2026-01-01",
            "csrf_token": csrf,
        },
    )

    headers = login_headers(client, tenant.slug, users["admin"].email)
    client.post(
        "/api/v1/issued-items",
        headers=headers,
        json={
            "account_id": str(account.id),
            "check_number": "9103",
            "amount": "60.00",
            "payee_name": "Vendor C",
            "issue_date": "2026-01-01",
        },
    )

    entries = db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id).all()
    channels = {e.channel.value for e in entries}
    assert channels == {"web", "api"}


def test_decision_recommend_and_decide_appear_in_audit_log(client, db_session, tenant_factory):
    from datetime import date
    from decimal import Decimal

    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
    from openclear.repositories.exception_repo import ExceptionRepository
    from openclear.services import issued_item_service

    tenant, account, users = tenant_factory.make(slug="audit-web-decision", require_dual_control=True)
    issued_item_service.create_issued_item(
        db_session,
        tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number="9201", amount=Decimal("100.00"), payee_name="Vendor", issue_date=date(2026, 1, 1)
        ),
        submitted_by_user_id=users["preparer"].id,
    )
    db_session.commit()
    ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(account_id=account.id, check_number="9201", presented_amount=Decimal("999.00"), presented_date=date(2026, 1, 10)),
    )
    db_session.commit()
    exception = ExceptionRepository(db_session, tenant.id).list()[0]

    preparer_csrf = _login(client, tenant.slug, users["preparer"].email)
    client.post(
        f"/ui/exceptions/{exception.id}/recommend",
        data={"outcome": "return", "reason_code": "amount_mismatch", "notes": "", "csrf_token": preparer_csrf},
    )
    approver_csrf = _login(client, tenant.slug, users["approver"].email)
    client.post(
        f"/ui/exceptions/{exception.id}/decide",
        data={"outcome": "return", "reason_code": "amount_mismatch", "notes": "", "csrf_token": approver_csrf},
    )

    admin_csrf = _login(client, tenant.slug, users["admin"].email)
    assert admin_csrf  # admin has audit_log:read by default
    log_page = client.get("/ui/audit-log")
    assert "exception.recommend" in log_page.text
    assert "exception.decide" in log_page.text


def test_verify_chain_page_shows_valid_then_tampered(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="audit-web-verify")
    csrf = _login(client, tenant.slug, users["admin"].email)

    client.post(
        "/ui/issued-items",
        data={
            "account_id": str(account.id),
            "check_number": "9301",
            "amount": "10.00",
            "payee_name": "Vendor",
            "issue_date": "2026-01-01",
            "csrf_token": csrf,
        },
    )

    valid_resp = client.get("/ui/audit-log/verify")
    assert "chain verified" in valid_resp.text

    entry = db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id).first()
    entry.summary = "tampered summary"
    db_session.commit()

    tampered_resp = client.get("/ui/audit-log/verify")
    assert "CHAIN BROKEN" in tampered_resp.text


def test_user_and_security_group_actions_appear_in_audit_log(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="audit-web-users-groups")
    csrf = _login(client, tenant.slug, users["admin"].email)

    client.post(
        "/ui/security-groups", data={"csrf_token": csrf, "name": "AP Clerk", "permissions": ["issued_item:read"]}
    )

    log_page = client.get("/ui/audit-log")
    assert "security_group.create" in log_page.text
    assert "AP Clerk" in log_page.text
