# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import date
from decimal import Decimal

from openclear.domain.ach_transaction import AchTransactionType
from openclear.domain.bulk_upload_file import BulkUploadKind
from openclear.domain.exception_item import ExceptionStatus
from openclear.domain.issued_item import IssuedItemStatus
from openclear.domain.paid_item import PaidItemSettlementStatus
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.services import (
    account_service,
    bulk_upload_file_service,
    bulk_upload_reversal_service,
    decision_service,
    issued_item_service,
    security_group_service,
    user_service,
)
from tests.conftest import TenantFactory


def _make_upload(db_session, tenant_id, user_id, kind=BulkUploadKind.ISSUED_ITEMS):
    return bulk_upload_file_service.record_uploaded_file(
        db_session,
        tenant_id,
        kind=kind,
        filename="upload.csv",
        content_type="text/csv",
        data=b"irrelevant,contents\n1,2\n",
        uploaded_by_user_id=user_id,
    )


def test_back_out_returns_none_for_unknown_upload(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="reversal-unknown-upload")
    result = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, uuid.uuid4(), actor_user_id=users["admin"].id, channel="web"
    )
    assert result is None


def test_back_out_returns_none_when_nothing_tracked(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="reversal-nothing-tracked")
    upload = _make_upload(db_session, tenant.id, users["admin"].id)
    db_session.commit()

    result = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert result is None


def test_back_out_issued_items_voids_and_is_one_way(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="reversal-issued-items")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.ISSUED_ITEMS)
    item = issued_item_service.create_issued_item(
        db_session,
        tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number="9001", amount=Decimal("100.00"), payee_name="Vendor", issue_date=date(2026, 1, 1)
        ),
        submitted_by_user_id=users["admin"].id,
    )
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="issued_item", resource_id=item.id, row_label="row 2"
    )
    db_session.commit()

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert len(results) == 1
    assert results[0].success is True

    db_session.refresh(item)
    assert item.status == IssuedItemStatus.VOIDED

    # one-way: the upload is now marked backed out, a second attempt is refused
    again = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert again is None


def test_back_out_skips_already_voided_issued_item(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="reversal-issued-already-voided")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.ISSUED_ITEMS)
    item = issued_item_service.create_issued_item(
        db_session,
        tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number="9002", amount=Decimal("50.00"), payee_name="Vendor", issue_date=date(2026, 1, 1)
        ),
        submitted_by_user_id=users["admin"].id,
    )
    issued_item_service.void_issued_item(db_session, tenant.id, item.id, "manually voided already")
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="issued_item", resource_id=item.id, row_label="row 2"
    )
    db_session.commit()

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert results[0].success is False
    assert "Already voided" in results[0].error


def test_back_out_skips_already_paid_issued_item(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="reversal-issued-already-paid")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.ISSUED_ITEMS)
    item = issued_item_service.create_issued_item(
        db_session,
        tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number="9003", amount=Decimal("75.00"), payee_name="Vendor", issue_date=date(2026, 1, 1)
        ),
        submitted_by_user_id=users["admin"].id,
    )
    ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(account_id=account.id, check_number="9003", presented_amount=Decimal("75.00"), presented_date=date(2026, 1, 5)),
    )
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="issued_item", resource_id=item.id, row_label="row 2"
    )
    db_session.commit()
    db_session.refresh(item)
    assert item.status == IssuedItemStatus.PAID

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert results[0].success is False
    assert "already been paid" in results[0].error


def test_back_out_tenant_membership_deactivates(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="reversal-membership")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.USERS)
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    new_user = user_service.create_user_with_membership(
        db_session, tenant.id, email="bulk-added@example.com", password=TenantFactory.PASSWORD, security_group_id=preparer_group.id
    )
    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=new_user.id)[0]
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="tenant_membership", resource_id=membership.id, row_label="row 2"
    )
    db_session.commit()

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert results[0].success is True
    db_session.refresh(membership)
    assert membership.is_active is False


def test_back_out_account_is_record_only(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="reversal-account")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.ACCOUNTS)
    new_account = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="NEW-1", name="New Account")
    )
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="account", resource_id=new_account.id, row_label="row 2"
    )
    db_session.commit()

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert results[0].success is True

    tracked = bulk_upload_reversal_service.list_created_records(db_session, tenant.id, upload.id)
    assert tracked[0].reversed_at is not None
    # record-only: the account row itself is completely untouched
    db_session.refresh(new_account)
    assert new_account.account_number == "NEW-1"


def test_back_out_ach_transaction_is_record_only(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="reversal-ach-txn")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.ACH_TRANSACTIONS)
    txn = ingest_ach_transaction(
        db_session,
        tenant.id,
        AchTransactionSubmission(
            account_id=account.id, originator_id="ORIG1", originator_name="Acme", amount=Decimal("10.00"),
            transaction_type=AchTransactionType.CREDIT, sec_code="PPD", trace_number="T1", effective_date=date(2026, 1, 1),
        ),
    )
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="ach_transaction", resource_id=txn.id, row_label="row 2"
    )
    db_session.commit()

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert results[0].success is True


def test_back_out_matched_paid_item_reverts_issued_item_to_outstanding(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="reversal-paid-matched")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.CHECK_IMAGES)
    issued = issued_item_service.create_issued_item(
        db_session,
        tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number="7001", amount=Decimal("200.00"), payee_name="Vendor", issue_date=date(2026, 1, 1)
        ),
        submitted_by_user_id=users["admin"].id,
    )
    paid_item = ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(account_id=account.id, check_number="7001", presented_amount=Decimal("200.00"), presented_date=date(2026, 1, 5)),
    )
    assert paid_item.match_status.value == "matched"
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="paid_item", resource_id=paid_item.id, row_label="row 2"
    )
    db_session.commit()
    db_session.refresh(issued)
    assert issued.status == IssuedItemStatus.PAID

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert results[0].success is True
    assert results[0].note is None

    db_session.refresh(issued)
    db_session.refresh(paid_item)
    assert issued.status == IssuedItemStatus.OUTSTANDING
    assert paid_item.settlement_status == PaidItemSettlementStatus.REVERSED


def test_back_out_matched_paid_item_leaves_note_if_issued_item_status_already_changed(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="reversal-paid-matched-changed")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.CHECK_IMAGES)
    issued = issued_item_service.create_issued_item(
        db_session,
        tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number="7002", amount=Decimal("300.00"), payee_name="Vendor", issue_date=date(2026, 1, 1)
        ),
        submitted_by_user_id=users["admin"].id,
    )
    paid_item = ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(account_id=account.id, check_number="7002", presented_amount=Decimal("300.00"), presented_date=date(2026, 1, 5)),
    )
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="paid_item", resource_id=paid_item.id, row_label="row 2"
    )
    # simulate the issued item's status having independently changed since (e.g. manually voided)
    issued.status = IssuedItemStatus.VOIDED
    db_session.commit()

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert results[0].success is True
    assert "already changed" in results[0].note

    db_session.refresh(paid_item)
    assert paid_item.settlement_status == PaidItemSettlementStatus.REVERSED
    db_session.refresh(issued)
    assert issued.status == IssuedItemStatus.VOIDED  # left alone, not clobbered


def test_back_out_unmatched_paid_item_withdraws_open_exception(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="reversal-paid-exception-open")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.CHECK_IMAGES)
    paid_item = ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(account_id=account.id, check_number="8001", presented_amount=Decimal("40.00"), presented_date=date(2026, 1, 5)),
    )
    assert paid_item.match_status.value == "exception"
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)[0]
    assert exception.status == ExceptionStatus.OPEN
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="paid_item", resource_id=paid_item.id, row_label="row 2"
    )
    db_session.commit()

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert results[0].success is True
    assert results[0].note is None

    db_session.refresh(exception)
    db_session.refresh(paid_item)
    assert exception.status == ExceptionStatus.WITHDRAWN
    assert paid_item.settlement_status == PaidItemSettlementStatus.REVERSED


def test_back_out_unmatched_paid_item_leaves_already_decided_exception_alone(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="reversal-paid-exception-decided")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.CHECK_IMAGES)
    paid_item = ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(account_id=account.id, check_number="8002", presented_amount=Decimal("60.00"), presented_date=date(2026, 1, 5)),
    )
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)[0]
    db_session.commit()

    from openclear.db.tenancy import TenantContext
    from openclear.domain.decision import DecisionOutcome

    ctx = TenantContext(
        tenant_id=tenant.id,
        user_id=users["approver"].id,
        security_group_id=uuid.uuid4(),
        permissions=frozenset({"exception:decide"}),
        tenant_slug=tenant.slug,
        tenant_name=tenant.name,
        accent_color=None,
        has_logo=False,
        has_favicon=False,
        customer_id=None,
        customer_name=None,
    )
    result = decision_service.decide(
        db_session, tenant.id, exception.id, ctx, outcome=DecisionOutcome.PAY, reason_code="known payee", notes=None
    )
    assert result.error is None
    db_session.commit()
    db_session.refresh(paid_item)
    assert paid_item.settlement_status == PaidItemSettlementStatus.PAID  # final at once: no cut-off ahead (REVIEW_1_33_PLAN.md RV-D1)

    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="paid_item", resource_id=paid_item.id, row_label="row 3"
    )
    db_session.commit()

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert results[0].success is True
    assert "already been pay" in results[0].note

    db_session.refresh(exception)
    db_session.refresh(paid_item)
    assert exception.status == ExceptionStatus.PAY  # left alone, not overwritten
    assert paid_item.settlement_status == PaidItemSettlementStatus.REVERSED


def test_back_out_paid_item_already_returned_by_a_decision_is_refused(db_session, tenant_factory):
    """The return went to the core when the decision became final; backing the file out can't take it back."""
    tenant, account, users = tenant_factory.make(slug="reversal-paid-returned")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.PAID_ITEMS)
    paid_item = ingest_paid_item(
        db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number="9301", presented_amount=Decimal("55.00"),
                                                  presented_date=date(2026, 10, 1)),
    )
    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid_item.id)[0]
    db_session.commit()
    from openclear.db.tenancy import TenantContext
    from openclear.domain.decision import DecisionOutcome

    ctx = TenantContext(tenant_id=tenant.id, user_id=users["approver"].id, security_group_id=uuid.uuid4(),
                        permissions=frozenset({"exception:decide"}), tenant_slug=tenant.slug, tenant_name=tenant.name, accent_color=None,
                        has_logo=False, has_favicon=False, customer_id=None, customer_name=None)
    assert decision_service.decide(db_session, tenant.id, exception.id, ctx, outcome=DecisionOutcome.RETURN, reason_code="fraud",
                                   notes=None).error is None
    db_session.commit()
    bulk_upload_reversal_service.track_created_record(db_session, tenant.id, upload.id, resource_type="paid_item",
                                                      resource_id=paid_item.id, row_label="row 1")
    db_session.commit()
    results = bulk_upload_reversal_service.back_out_upload(db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web")
    assert results[0].success is False and "processed as a return" in results[0].error
    db_session.refresh(paid_item)
    assert paid_item.settlement_status == PaidItemSettlementStatus.RETURNED


def test_back_out_already_reversed_paid_item_is_skipped(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="reversal-paid-already-reversed")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.CHECK_IMAGES)
    paid_item = ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(account_id=account.id, check_number="8003", presented_amount=Decimal("15.00"), presented_date=date(2026, 1, 5)),
    )
    paid_item.settlement_status = PaidItemSettlementStatus.REVERSED
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="paid_item", resource_id=paid_item.id, row_label="row 2"
    )
    db_session.commit()

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    assert results[0].success is False
    assert "Already backed out" in results[0].error


def test_check_image_sibling_record_is_marked_reversed_without_its_own_result_row(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="reversal-check-image-sibling")
    upload = _make_upload(db_session, tenant.id, users["admin"].id, kind=BulkUploadKind.CHECK_IMAGES)
    paid_item = ingest_paid_item(
        db_session,
        tenant.id,
        PaidItemSubmission(account_id=account.id, check_number="8004", presented_amount=Decimal("20.00"), presented_date=date(2026, 1, 5)),
    )
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="paid_item", resource_id=paid_item.id, row_label="row 2"
    )
    fake_check_image_id = uuid.uuid4()
    bulk_upload_reversal_service.track_created_record(
        db_session, tenant.id, upload.id, resource_type="check_image", resource_id=fake_check_image_id, row_label="row 2"
    )
    db_session.commit()

    results = bulk_upload_reversal_service.back_out_upload(
        db_session, tenant.id, upload.id, actor_user_id=users["admin"].id, channel="web"
    )
    # both tracked records share row_label "row 2" -> exactly one result row, not two
    assert len(results) == 1

    tracked = bulk_upload_reversal_service.list_created_records(db_session, tenant.id, upload.id)
    assert all(t.reversed_at is not None for t in tracked)
