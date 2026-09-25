# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Tracks which records a bulk upload created, and backs them out later.

Every other service module in this app stays audit-log-agnostic (the calling web/api
router logs the audit entry right after a service call succeeds) — `back_out_upload`
deliberately breaks that convention and logs its own per-row audit entries, because it
dispatches across several different resource types in a single loop and the router
calling it has no way to know, ahead of time, which specific action (void/deactivate/
reverse/withdraw/record-only) each tracked row will resolve to."""

import uuid
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from openclear.bulk_import.results import BulkFileRowResult
from openclear.domain.bulk_upload_created_record import BulkUploadCreatedRecord
from openclear.domain.exception_item import ExceptionItem, ExceptionStatus
from openclear.domain.issued_item import IssuedItemStatus
from openclear.domain.paid_item import PaidItemMatchStatus, PaidItemSettlementStatus
from openclear.repositories.bulk_upload_created_record_repo import BulkUploadCreatedRecordRepository
from openclear.repositories.bulk_upload_file_repo import BulkUploadFileRepository
from openclear.repositories.issued_item_repo import IssuedItemRepository
from openclear.repositories.paid_item_repo import PaidItemRepository
from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.services import audit_log_service, issued_item_service, user_service


def track_created_record(
    session: Session,
    tenant_id: uuid.UUID,
    bulk_upload_file_id: uuid.UUID,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    row_label: str,
) -> BulkUploadCreatedRecord:
    record = BulkUploadCreatedRecord(
        bulk_upload_file_id=bulk_upload_file_id, resource_type=resource_type, resource_id=resource_id, row_label=row_label
    )
    BulkUploadCreatedRecordRepository(session, tenant_id).add(record)
    session.flush()
    return record


def list_created_records(session: Session, tenant_id: uuid.UUID, bulk_upload_file_id: uuid.UUID) -> list[BulkUploadCreatedRecord]:
    return BulkUploadCreatedRecordRepository(session, tenant_id).list(bulk_upload_file_id=bulk_upload_file_id)


def _back_out_issued_item(
    session: Session, tenant_id: uuid.UUID, resource_id: uuid.UUID, *, scoped_customer_id: uuid.UUID | None,
    actor_user_id: uuid.UUID, channel: Literal["web", "api"], row_label: str,
) -> tuple[bool, str | None]:
    item = IssuedItemRepository(session, tenant_id, scoped_customer_id).get(resource_id)
    if item is None:
        return False, "Issued item no longer exists."
    if item.status == IssuedItemStatus.VOIDED:
        return False, "Already voided."
    if item.status == IssuedItemStatus.PAID:
        return False, "Cannot back out: this item has already been paid."

    issued_item_service.void_issued_item(
        session, tenant_id, item.id, "Backed out via bulk upload reversal", scoped_customer_id=scoped_customer_id
    )
    audit_log_service.record_action(
        session,
        tenant_id,
        actor_user_id=actor_user_id,
        channel=channel,
        action="issued_item.void",
        summary=f"Issued item voided as part of backing out a bulk upload ({row_label})",
        resource_type="issued_item",
        resource_id=item.id,
    )
    return True, None


def _back_out_tenant_membership(
    session: Session, tenant_id: uuid.UUID, resource_id: uuid.UUID, *, actor_user_id: uuid.UUID,
    channel: Literal["web", "api"], row_label: str,
) -> tuple[bool, str | None]:
    membership = TenantMembershipRepository(session, tenant_id).get(resource_id)
    if membership is None:
        return False, "Membership no longer exists."
    if not membership.is_active:
        return False, "Already deactivated."

    user_service.deactivate_membership(session, tenant_id, membership.id)
    audit_log_service.record_action(
        session,
        tenant_id,
        actor_user_id=actor_user_id,
        channel=channel,
        action="user.deactivate",
        summary=f"Access deactivated as part of backing out a bulk upload ({row_label})",
        resource_type="user",
        resource_id=membership.user_id,
    )
    return True, None


def _withdraw_exception_if_open(
    session: Session, tenant_id: uuid.UUID, paid_item_id: uuid.UUID, *, actor_user_id: uuid.UUID, channel: Literal["web", "api"]
) -> str | None:
    """Returns a note describing what happened, or None if there was nothing to do
    (no exception, or it was already decided/escalated and was left as-is)."""
    exception = (
        session.query(ExceptionItem)
        .filter(ExceptionItem.source_item_id == paid_item_id, ExceptionItem.network_code == "check")
        .one_or_none()
    )
    if exception is None:
        return None
    if exception.status not in (ExceptionStatus.OPEN, ExceptionStatus.PENDING_APPROVAL):
        return f"its exception had already been {exception.status.value} and was left as-is"

    exception.status = ExceptionStatus.WITHDRAWN
    session.flush()
    audit_log_service.record_action(
        session,
        tenant_id,
        actor_user_id=actor_user_id,
        channel=channel,
        action="exception.withdraw",
        summary="Exception withdrawn because its underlying paid item was backed out via a bulk upload reversal",
        resource_type="exception_item",
        resource_id=exception.id,
    )
    return None


def _back_out_paid_item(
    session: Session, tenant_id: uuid.UUID, resource_id: uuid.UUID, *, scoped_customer_id: uuid.UUID | None,
    actor_user_id: uuid.UUID, channel: Literal["web", "api"], row_label: str,
) -> tuple[bool, str | None]:
    paid_item = PaidItemRepository(session, tenant_id, scoped_customer_id).get(resource_id)
    if paid_item is None:
        return False, "Paid item no longer exists."
    if paid_item.settlement_status == PaidItemSettlementStatus.REVERSED:
        return False, "Already backed out."
    if paid_item.settlement_status == PaidItemSettlementStatus.RETURNED:
        return False, "Cannot back out: this item has already been processed as a return."

    note: str | None = None
    if paid_item.match_status == PaidItemMatchStatus.MATCHED and paid_item.matched_issued_item_id:
        issued_item = IssuedItemRepository(session, tenant_id, scoped_customer_id).get(paid_item.matched_issued_item_id)
        if issued_item is not None and issued_item.status == IssuedItemStatus.PAID:
            issued_item.status = IssuedItemStatus.OUTSTANDING
            session.flush()
        else:
            note = "its linked issued item's status had already changed since and was left as-is"
    elif paid_item.match_status == PaidItemMatchStatus.EXCEPTION:
        note = _withdraw_exception_if_open(session, tenant_id, paid_item.id, actor_user_id=actor_user_id, channel=channel)

    paid_item.settlement_status = PaidItemSettlementStatus.REVERSED
    session.flush()
    audit_log_service.record_action(
        session,
        tenant_id,
        actor_user_id=actor_user_id,
        channel=channel,
        action="paid_item.reverse",
        summary=f"Paid item reversed as part of backing out a bulk upload ({row_label})",
        resource_type="paid_item",
        resource_id=paid_item.id,
    )
    return True, note


def _back_out_record_only(
    session: Session, tenant_id: uuid.UUID, resource_type: str, resource_id: uuid.UUID, *, actor_user_id: uuid.UUID,
    channel: Literal["web", "api"], row_label: str,
) -> tuple[bool, str | None]:
    """Accounts and ACH transactions have no deactivation/reversal concept in this app —
    backing these out is a tracking/audit statement, not a domain-state change (see the
    plan's confirmed scope decision)."""
    audit_log_service.record_action(
        session,
        tenant_id,
        actor_user_id=actor_user_id,
        channel=channel,
        action=f"{resource_type}.mark_upload_reversed",
        summary=f"{resource_type.replace('_', ' ').title()} marked as originating from a since-backed-out bulk upload ({row_label})",
        resource_type=resource_type,
        resource_id=resource_id,
    )
    return True, None


def back_out_upload(
    session: Session,
    tenant_id: uuid.UUID,
    upload_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    channel: Literal["web", "api"],
    scoped_customer_id: uuid.UUID | None = None,
) -> list[BulkFileRowResult] | None:
    """Backs out every record a bulk upload created. One DB transaction per tracked
    row/group, same per-row isolation as every bulk importer in this app. Returns None
    if the upload doesn't exist, has nothing tracked (predates this feature, or nothing
    it created ever succeeded), or has already been backed out — callers turn that into
    a 404/flash message, not a results page. `scoped_customer_id` constrains every
    per-row lookup (and the upload record itself) the same way every other write path in
    this app does, so a customer-scoped caller can't reach across customer boundaries."""
    upload = BulkUploadFileRepository(session, tenant_id, scoped_customer_id).get(upload_id)
    if upload is None or upload.backed_out_at is not None:
        return None

    tracked = list_created_records(session, tenant_id, upload_id)
    if not tracked:
        return None

    by_row_label: dict[str, list[BulkUploadCreatedRecord]] = {}
    for record in tracked:
        by_row_label.setdefault(record.row_label, []).append(record)

    results: list[BulkFileRowResult] = []
    for row_label, records in by_row_label.items():
        driver = next((r for r in records if r.resource_type != "check_image"), records[0])
        siblings = [r for r in records if r.id != driver.id]
        try:
            if driver.resource_type == "issued_item":
                success, note = _back_out_issued_item(
                    session, tenant_id, driver.resource_id, scoped_customer_id=scoped_customer_id,
                    actor_user_id=actor_user_id, channel=channel, row_label=row_label,
                )
            elif driver.resource_type == "tenant_membership":
                success, note = _back_out_tenant_membership(
                    session, tenant_id, driver.resource_id, actor_user_id=actor_user_id, channel=channel, row_label=row_label,
                )
            elif driver.resource_type == "paid_item":
                success, note = _back_out_paid_item(
                    session, tenant_id, driver.resource_id, scoped_customer_id=scoped_customer_id,
                    actor_user_id=actor_user_id, channel=channel, row_label=row_label,
                )
            else:  # account / ach_transaction (record-only)
                success, note = _back_out_record_only(
                    session, tenant_id, driver.resource_type, driver.resource_id,
                    actor_user_id=actor_user_id, channel=channel, row_label=row_label,
                )

            if success:
                now = datetime.now(timezone.utc)
                driver.reversed_at = now
                driver.reversed_by_user_id = actor_user_id
                for sibling in siblings:
                    sibling.reversed_at = now
                    sibling.reversed_by_user_id = actor_user_id
                session.commit()
                results.append(BulkFileRowResult(row_label=row_label, success=True, created_id=driver.resource_id, note=note))
            else:
                session.rollback()
                results.append(BulkFileRowResult(row_label=row_label, success=False, created_id=driver.resource_id, error=note))
        except Exception as exc:  # noqa: BLE001 — isolate row failures, same as every bulk importer
            session.rollback()
            results.append(BulkFileRowResult(row_label=row_label, success=False, created_id=driver.resource_id, error=str(exc)))

    upload.backed_out_at = datetime.now(timezone.utc)
    upload.backed_out_by_user_id = actor_user_id
    session.commit()
    return results
