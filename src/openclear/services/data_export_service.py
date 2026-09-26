# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import json
import time
import uuid
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from openclear.db.tenancy import bind_session_to_tenant
from openclear.config import get_settings
from openclear.domain.customer import Customer
from openclear.domain.data_export_job import DataExportJob, DataExportJobStatus
from openclear.domain.decision import Decision
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.tenant import Tenant
from openclear.repositories.ach_authorization_repo import AchAuthorizationRepository
from openclear.repositories.ach_transaction_repo import AchTransactionRepository
from openclear.repositories.account_repo import AccountRepository
from openclear.repositories.audit_log_repo import AuditLogRepository
from openclear.repositories.bulk_upload_file_repo import BulkUploadFileRepository
from openclear.repositories.check_image_repo import CheckImageRepository
from openclear.repositories.customer_repo import CustomerRepository
from openclear.repositories.decision_repo import DecisionRepository
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.repositories.issued_item_repo import IssuedItemRepository
from openclear.repositories.paid_item_repo import PaidItemRepository
from openclear.repositories.sso_connection_repo import SsoConnectionRepository
from openclear.repositories.sso_group_mapping_repo import SsoGroupMappingRepository
from openclear.repositories.stop_payment_repo import StopPaymentRepository
from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.repositories.user_repo import UserRepository
from openclear.services import data_export_storage, security_group_service

# The only two genuinely secret columns in the schema — never included in an export,
# regardless of which entity they appear on. Everything else here is either already
# non-sensitive or (like a bcrypt hash) simply useless to a new platform anyway.
_SECRET_COLUMNS = {"hashed_password", "client_secret_encrypted"}


class ExportTimedOut(Exception):
    """Raised by _build_archive when the export's time budget (config.py's
    data_export_timeout_seconds, or a tenant's own override) is exhausted before every
    entity/file has been written — caught by run_export_job's existing broad except,
    same as ExportRowLimitExceeded below."""


class ExportRowLimitExceeded(Exception):
    """Raised by _build_archive when a single entity has more rows than
    config.Settings.data_export_max_rows_per_entity — a technical ceiling on archive
    size/build cost, not a per-tenant policy value, so there's no override for it."""


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Generic SQLAlchemy row -> JSON-safe dict, stripping _SECRET_COLUMNS. Shared by
    every exporter below that doesn't need custom field composition, so no exporter has
    to hand-map its own columns."""
    result: dict[str, Any] = {}
    for column in row.__table__.columns:
        if column.name in _SECRET_COLUMNS:
            continue
        value = getattr(row, column.name)
        if isinstance(value, uuid.UUID):
            value = str(value)
        elif isinstance(value, enum.Enum):
            value = value.value
        elif isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, (datetime, date)):
            value = value.isoformat()
        result[column.name] = value
    return result


def _check_image_rows(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> list:
    return CheckImageRepository(session, tenant_id, customer_id).list()


def _decision_rows(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> list:
    repo = DecisionRepository(session, tenant_id)
    if customer_id is None:
        return repo.list()
    # One query: a customer's decisions are those on its own exceptions.
    stmt = repo.query().join(ExceptionItem, ExceptionItem.id == Decision.exception_item_id).where(
        ExceptionItem.customer_id == customer_id
    )
    return list(session.execute(stmt).scalars().all())


def _users_dicts(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> list[dict]:
    """Custom composition, not _row_to_dict: pulls in the user's email and the
    membership's resolved security group NAME (never the whole security_group table for
    a customer-scoped export — see the scoping table in docs/ARCHITECTURE.md, "Data exports") — and never the password
    hash, which isn't even a column on TenantMembership to begin with. The dict is still
    run through _SECRET_COLUMNS below before being kept, purely as a mechanical
    safety net — belt-and-suspenders against a future edit to this function
    reintroducing a secret field by hand, not because one is emitted today."""
    memberships = TenantMembershipRepository(session, tenant_id).list(customer_id=customer_id)
    user_repo = UserRepository(session)
    out = []
    for membership in memberships:
        user = user_repo.get(membership.user_id)
        group = security_group_service.get_security_group(session, tenant_id, membership.security_group_id)
        row = {
            "membership_id": str(membership.id),
            "email": user.email if user else None,
            "is_active": membership.is_active,
            "security_group_name": group.name if group else None,
            "customer_id": str(membership.customer_id) if membership.customer_id else None,
            "created_at": membership.created_at.isoformat(),
        }
        out.append({k: v for k, v in row.items() if k not in _SECRET_COLUMNS})
    return out


def _sso_group_mapping_dicts(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> list[dict]:
    connections = SsoConnectionRepository(session, tenant_id).list(customer_id=customer_id)
    connection_ids = {c.id for c in connections}
    mappings = SsoGroupMappingRepository(session, tenant_id).list()
    return [_row_to_dict(m) for m in mappings if m.connection_id in connection_ids]


# Included in both tenant-wide and customer-scoped exports.
_SCOPED_EXPORTERS: list[tuple[str, Any]] = [
    ("accounts", lambda s, t, c: [_row_to_dict(r) for r in AccountRepository(s, t, c).list()]),
    ("issued_items", lambda s, t, c: [_row_to_dict(r) for r in IssuedItemRepository(s, t, c).list()]),
    ("stop_payments", lambda s, t, c: [_row_to_dict(r) for r in StopPaymentRepository(s, t, c).list()]),
    ("paid_items", lambda s, t, c: [_row_to_dict(r) for r in PaidItemRepository(s, t, c).list()]),
    ("ach_authorizations", lambda s, t, c: [_row_to_dict(r) for r in AchAuthorizationRepository(s, t, c).list()]),
    ("ach_transactions", lambda s, t, c: [_row_to_dict(r) for r in AchTransactionRepository(s, t, c).list()]),
    ("exceptions", lambda s, t, c: [_row_to_dict(r) for r in ExceptionRepository(s, t, c).list()]),
    ("decisions", lambda s, t, c: [_row_to_dict(r) for r in _decision_rows(s, t, c)]),
    ("check_images", lambda s, t, c: [_row_to_dict(r) for r in _check_image_rows(s, t, c)]),
    ("users", _users_dicts),
    ("sso_connections", lambda s, t, c: [_row_to_dict(r) for r in SsoConnectionRepository(s, t).list(customer_id=c)]),
    ("sso_group_mappings", _sso_group_mapping_dicts),
]

# Tenant-wide exports only — see docs/ARCHITECTURE.md ("Data exports") for why each is excluded from
# a customer-scoped export (not cleanly attributable to one customer, or would leak the
# tenant's own configuration/action history to a single customer's admin).
_TENANT_WIDE_ONLY_EXPORTERS: list[tuple[str, Any]] = [
    ("customers", lambda s, t, c: [_row_to_dict(r) for r in CustomerRepository(s, t).list()]),
    ("bulk_upload_files", lambda s, t, c: [_row_to_dict(r) for r in BulkUploadFileRepository(s, t).list()]),
    ("audit_log", lambda s, t, c: [_row_to_dict(r) for r in AuditLogRepository(s, t).list()]),
]


def request_export(
    session: Session, tenant_id: uuid.UUID, requested_by_user_id: uuid.UUID, customer_id: uuid.UUID | None = None
) -> DataExportJob:
    job = DataExportJob(
        tenant_id=tenant_id, customer_id=customer_id, requested_by_user_id=requested_by_user_id,
        status=DataExportJobStatus.PENDING,
    )
    session.add(job)
    session.flush()
    return job


def _apply_rls_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    bind_session_to_tenant(session, tenant_id)


def _add_file_if_exists(zf: zipfile.ZipFile, source_path: str | None, archive_name: str) -> None:
    if not source_path:
        return
    path = Path(source_path)
    if path.exists():
        zf.write(path, archive_name)


def _build_archive(
    session: Session,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID | None,
    destination: Path,
    *,
    timeout_seconds: int,
    max_rows_per_entity: int,
) -> None:
    """Checks the time budget (`timeout_seconds`, resolved by run_export_job from the
    tenant's own override or config.py's global default) before starting each named
    entity's export and before each individual file copy — a cooperative check between
    units of work, not a preemptive per-row streaming rewrite of every repository. A
    single entity whose own query is itself slow isn't interrupted mid-flight; this
    bounds how much NEW work starts once the budget is spent, and (via
    `max_rows_per_entity`) how large any one entity's own JSON blob can grow before it's
    written — proportional to this being a Low/Low-Medium finding, same "sum sizes before
    reading" spirit as bulk_import/zip_import.py's zip-bomb guard rather than a full
    pagination rewrite."""
    exporters = list(_SCOPED_EXPORTERS)
    if customer_id is None:
        exporters += _TENANT_WIDE_ONLY_EXPORTERS

    deadline = time.monotonic() + timeout_seconds

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, fn in exporters:
            if time.monotonic() > deadline:
                raise ExportTimedOut(f"Export exceeded its {timeout_seconds}s time limit before reaching {name!r}.")
            rows = fn(session, tenant_id, customer_id)
            if len(rows) > max_rows_per_entity:
                raise ExportRowLimitExceeded(
                    f"{name!r} has {len(rows)} rows, exceeding the {max_rows_per_entity}-row export limit."
                )
            zf.writestr(f"data/{name}.json", json.dumps(rows, indent=2))

        for check_image in _check_image_rows(session, tenant_id, customer_id):
            if time.monotonic() > deadline:
                raise ExportTimedOut(f"Export exceeded its {timeout_seconds}s time limit while copying check images.")
            if check_image.front_image_path:
                _add_file_if_exists(zf, check_image.front_image_path, f"files/check_images/{Path(check_image.front_image_path).name}")
            if check_image.back_image_path:
                _add_file_if_exists(zf, check_image.back_image_path, f"files/check_images/{Path(check_image.back_image_path).name}")

        if customer_id is None:
            for upload in BulkUploadFileRepository(session, tenant_id).list():
                if time.monotonic() > deadline:
                    raise ExportTimedOut(f"Export exceeded its {timeout_seconds}s time limit while copying bulk upload files.")
                _add_file_if_exists(zf, upload.storage_path, f"files/bulk_uploads/{Path(upload.storage_path).name}")

            tenant = session.get(Tenant, tenant_id)
            zf.writestr("data/tenant.json", json.dumps(_row_to_dict(tenant), indent=2))
            if tenant.logo_path:
                _add_file_if_exists(zf, tenant.logo_path, f"files/branding/{Path(tenant.logo_path).name}")
            if tenant.favicon_path:
                _add_file_if_exists(zf, tenant.favicon_path, f"files/branding/{Path(tenant.favicon_path).name}")
        else:
            customer = session.get(Customer, customer_id)
            zf.writestr("data/customer.json", json.dumps(_row_to_dict(customer), indent=2))


def run_export_job(engine: Engine, job_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    """Runs from a FastAPI BackgroundTask (see web/routers/data_export.py) — opens its
    own Session bound to the request's engine, same pattern as
    networks/check/ocr_processing.py's own background-task entry point, since the
    request's own session is closed by the time this runs. Never lets an exception escape
    unhandled: any failure marks the job FAILED with a message, rather than silently
    leaving it stuck at RUNNING forever."""
    session = Session(bind=engine)
    try:
        _apply_rls_tenant(session, tenant_id)
        job = session.get(DataExportJob, job_id)
        if job is None:
            return
        job.status = DataExportJobStatus.RUNNING
        session.commit()
        _apply_rls_tenant(session, tenant_id)  # commit resets the transaction-local setting

        settings = get_settings()
        tenant = session.get(Tenant, tenant_id)
        timeout_seconds = (tenant.data_export_timeout_seconds if tenant else None) or settings.data_export_timeout_seconds

        destination = data_export_storage.export_archive_path(tenant_id, job_id)
        try:
            _build_archive(
                session, tenant_id, job.customer_id, destination,
                timeout_seconds=timeout_seconds, max_rows_per_entity=settings.data_export_max_rows_per_entity,
            )
        except Exception as exc:  # noqa: BLE001 — must never crash the background task
            job.status = DataExportJobStatus.FAILED
            job.error_message = str(exc)[:1000]
            job.completed_at = datetime.now(timezone.utc)
            session.commit()
            return

        job.status = DataExportJobStatus.COMPLETED
        job.archive_path = str(destination)
        job.size_bytes = destination.stat().st_size
        job.completed_at = datetime.now(timezone.utc)
        session.commit()
    finally:
        session.close()
