# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date, datetime, timezone
from decimal import Decimal

from openclear.domain.ach_transaction import AchTransactionType
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.domain.decision import DecisionOutcome
from openclear.domain.notification import Notification, NotificationChannel, NotificationStatus, NotificationType
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import (
    account_service,
    bulk_upload_file_service,
    customer_service,
    decision_service,
    issued_item_service,
    security_group_service,
    usage_metrics_service,
    user_service,
)
from openclear.db.tenancy import TenantContext
from tests.conftest import TenantFactory
import uuid


def _decision_ctx(tenant, user_id):
    return TenantContext(
        tenant_id=tenant.id, user_id=user_id, security_group_id=uuid.uuid4(), permissions=frozenset(),
        tenant_slug=tenant.slug, tenant_name=tenant.name, accent_color=None, has_logo=False, has_favicon=False,
        customer_id=None, customer_name=None,
    )


def _mismatched_check_exception(db_session, tenant, account, users, check_number):
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
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)[0]
    return paid_item, exception


def test_get_tenant_usage_counts_every_dimension(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="usage-metrics-full")

    # Census: a second, customer-scoped account + a customer + an extra user.
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="ACME-1", name="Acme Op", customer_id=customer.id)
    )
    db_session.commit()
    viewer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    user_service.create_user_with_membership(
        db_session, tenant.id, email="extra-usage-user@example.com", password="ExtraUser123!", security_group_id=viewer_group.id,
    )
    db_session.commit()

    # Activity, within the test period 2026-01-01..2026-01-31.
    _paid_item, exception = _mismatched_check_exception(db_session, tenant, account, users, "9001")
    result = decision_service.decide(
        db_session, tenant.id, exception.id, _decision_ctx(tenant, users["approver"].id),
        outcome=DecisionOutcome.RETURN, reason_code="test", notes=None,
    )
    db_session.commit()
    assert result.error is None

    ingest_ach_transaction(
        db_session, tenant.id,
        AchTransactionSubmission(
            account_id=account.id, originator_id="ORIGX", originator_name="Orig Co", amount=Decimal("50.00"),
            transaction_type=AchTransactionType.DEBIT, sec_code="WEB", trace_number="TRACE-USAGE-1", effective_date=date(2026, 1, 5),
        ),
    )
    db_session.commit()

    db_session.add(Notification(
        tenant_id=tenant.id, recipient_user_id=users["admin"].id, notification_type=NotificationType.ACCOUNT_UNLOCKED,
        channel=NotificationChannel.SMS, destination="+15551234567", subject=None, body="test",
        status=NotificationStatus.SENT, sent_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
    ))
    db_session.commit()

    bulk_upload_file_service.record_uploaded_file(
        db_session, tenant.id, kind=BulkUploadKind.ISSUED_ITEMS, filename="x.csv", content_type="text/csv",
        data=b"a,b\n1,2\n", uploaded_by_user_id=users["admin"].id,
    )
    db_session.commit()

    # created_at is stamped at insertion time (now), not the fictional 2026-01 business
    # dates used above for issue_date/presented_date/effective_date -- query a wide range
    # that safely covers "now" regardless of when this test actually runs. Exclusion of a
    # genuinely out-of-range item is covered separately below.
    usage = usage_metrics_service.get_tenant_usage(db_session, tenant, date(2000, 1, 1), date(2100, 12, 31))

    assert usage.tenant_id == tenant.id
    assert usage.customers == 1
    assert usage.accounts == 2
    assert usage.users == 6  # 5 tenant_factory defaults (admin/preparer/approver/viewer/bookkeeper) + 1 extra
    assert usage.paid_items_check == 1
    assert usage.paid_items_ach == 1
    assert usage.paid_items_total == 2
    assert usage.exceptions == 2  # the mismatched check + the ACH transaction (no authorization rule exists for its originator)
    assert usage.returns == 1
    assert usage.sms_notifications == 1
    assert usage.bulk_uploads == 1


def test_get_tenant_usage_excludes_out_of_range_activity(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="usage-metrics-out-of-range")
    _mismatched_check_exception(db_session, tenant, account, users, "9001")

    usage = usage_metrics_service.get_tenant_usage(db_session, tenant, date(2020, 1, 1), date(2020, 1, 31))

    assert usage.exceptions == 0
    assert usage.paid_items_check == 0


def test_get_tenant_usage_users_counts_distinct_humans_not_memberships(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="usage-metrics-distinct-users")

    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    db_session.commit()
    viewer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Viewer")
    # Grant the SAME already-existing admin user a second, customer-scoped membership --
    # must not double-count them.
    user_service.grant_multi_customer_access(
        db_session, tenant.id, email=users["admin"].email, password=TenantFactory.PASSWORD,
        security_group_id=viewer_group.id, customer_ids=[customer.id],
    )
    db_session.commit()

    usage = usage_metrics_service.get_tenant_usage(db_session, tenant, date(2026, 1, 1), date(2026, 1, 31))

    assert usage.users == 5  # unchanged -- admin's second membership doesn't add a 6th


def test_get_tenant_usage_returns_counts_regardless_of_decision_source(db_session, tenant_factory):
    from openclear.domain.customer_disposition_setting import DispositionMode
    from openclear.services import auto_disposition_service

    tenant, account, users = tenant_factory.make(slug="usage-metrics-auto-returns")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    customer_account = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="ACME-2", name="Acme Op", customer_id=customer.id)
    )
    db_session.commit()
    auto_disposition_service.set_disposition_setting(
        db_session, tenant.id, customer.id, "check",
        mode=DispositionMode.FIXED_RETURN, response_window_hours=1, default_ach_return_reason_id=None,
    )
    db_session.commit()

    _paid_item, exception = _mismatched_check_exception(db_session, tenant, customer_account, users, "9002")
    decision = auto_disposition_service.auto_decide_exception(db_session, exception)
    db_session.commit()
    assert decision is not None
    assert decision.outcome == DecisionOutcome.RETURN

    usage = usage_metrics_service.get_tenant_usage(db_session, tenant, date(2026, 1, 1), date(2026, 12, 31))
    assert usage.returns == 1


def test_get_all_tenants_usage_isolates_each_tenant(db_session, tenant_factory):
    tenant_a, account_a, users_a = tenant_factory.make(slug="usage-metrics-multi-a")
    tenant_b, account_b, users_b = tenant_factory.make(slug="usage-metrics-multi-b")

    _mismatched_check_exception(db_session, tenant_a, account_a, users_a, "A001")
    _mismatched_check_exception(db_session, tenant_b, account_b, users_b, "B001")
    _mismatched_check_exception(db_session, tenant_b, account_b, users_b, "B002")

    results = {r.tenant_slug: r for r in usage_metrics_service.get_all_tenants_usage(db_session, date(2026, 1, 1), date(2026, 12, 31))}

    assert results["usage-metrics-multi-a"].exceptions == 1
    assert results["usage-metrics-multi-b"].exceptions == 2
