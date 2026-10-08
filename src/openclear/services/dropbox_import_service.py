# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Auto-import dropbox — watches a directory tree an external system (a core-banking
export, an SFTP drop) can land files into for unattended import, reusing every existing
bulk-import parser/ingestion function. See config.py's auto_import_* settings.

Directory layout — isolation is structural: the scanner only ever reads a tenant's own
subtree and only ever passes that tenant's own id to ingestion, never inferred from file
content:

    <auto_import_dropbox_dir>/
      <tenant_slug>/
        issued_items/inbox/                  # tabular only
        ach_transactions/tabular/inbox/
        ach_transactions/nacha/inbox/
        checks/tabular/inbox/                 # paid items, no image
        checks/x937/inbox/
        checks/zip/inbox/
        <customer_number>/                    # optional — same kind/format subtree, customer-scoped
          issued_items/inbox/
          ...

Every inbox/ gets sibling processed/ and failed/ directories (auto-created) — a handled
file is moved, never deleted. Format is chosen by which subdirectory a file was dropped
into, not by sniffing content or extension — same "structural, not inferential"
isolation reasoning as tenant scoping itself.

Three callers share the functions in this module (scan_and_import_tenant /
scan_and_import_all_tenants): the in-app scheduler (workers/scheduler.py), the CLI
entry point (scripts/import_dropbox.py, for cron/Task Scheduler), and the on-demand API
trigger (api/v1/dropbox_import.py), which calls scan_and_import_tenant scoped to only
the calling tenant/customer — never scan_and_import_all_tenants.

There is no per-directory "auto-create accounts" toggle here, unlike the manual bulk-
upload forms: an unattended process silently materializing a new account with no human
reviewing the checkbox is a bigger risk than a row failing with a clear "no account
found" error that shows up in this file's audit trail for a person to act on. If a row
references an account that doesn't exist yet, it's reported as a row failure, not
auto-created.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.bulk_import.file_storage import file_digest
from openclear.config import get_settings
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.bulk_upload_file import BulkUploadKind, BulkUploadSource, BulkUploadStatus
from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant
from openclear.services import bulk_import_service, bulk_upload_file_service
from openclear.workers.jobs import jobs_queued

logger = logging.getLogger(__name__)

_ISSUED_ITEMS = "issued_items"
_ACH_TABULAR = "ach_transactions/tabular"
_ACH_NACHA = "ach_transactions/nacha"
_CHECKS_TABULAR = "checks/tabular"
_CHECKS_X937 = "checks/x937"
_CHECKS_ZIP = "checks/zip"

_ALL_KIND_SUBPATHS = (_ISSUED_ITEMS, _ACH_TABULAR, _ACH_NACHA, _CHECKS_TABULAR, _CHECKS_X937, _CHECKS_ZIP)
_TOP_LEVEL_KIND_DIRS = frozenset(p.split("/")[0] for p in _ALL_KIND_SUBPATHS)

# tenant.slug and customer.customer_number both become raw filesystem path segments
# below (root / tenant.slug, root / customer.customer_number) -- neither is otherwise
# restricted to path-safe characters (customer_number in particular is a freeform field
# any customer:manage-permitted user can set or edit via a plain web form). An allowlist
# (not a denylist) is used deliberately: pathlib's "/" silently discards everything to
# the left when the right-hand segment looks absolute (root / "/etc" == Path("/etc")),
# and ".." components are honored on every OS at actual filesystem-access time -- either
# one, unfiltered, lets one tenant's customer_number escape into a SIBLING tenant's own
# dropbox subtree (customer_number="../other-tenant-slug") or an arbitrary path elsewhere
# on disk (customer_number="/etc/cron.d"), defeating the isolation this whole feature
# exists to guarantee.
_SAFE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _is_safe_path_segment(value: str) -> bool:
    return bool(_SAFE_PATH_SEGMENT_RE.match(value))


@dataclass(frozen=True, slots=True)
class ImportedFileResult:
    tenant_slug: str
    customer_number: str | None
    kind_subpath: str
    filename: str
    outcome: Literal["imported", "duplicate", "failed"]
    succeeded_count: int | None = None
    failed_count: int | None = None
    error: str | None = None


def _bulk_upload_kind_for(kind_subpath: str) -> BulkUploadKind:
    if kind_subpath == _ISSUED_ITEMS:
        return BulkUploadKind.ISSUED_ITEMS
    if kind_subpath in (_ACH_TABULAR, _ACH_NACHA):
        return BulkUploadKind.ACH_TRANSACTIONS
    if kind_subpath == _CHECKS_TABULAR:
        return BulkUploadKind.PAID_ITEMS
    return BulkUploadKind.CHECK_IMAGES  # checks/x937, checks/zip


# Which import format each inbox holds (services/bulk_import_service.SPECS).
_FORMAT_BY_SUBPATH = {
    _ISSUED_ITEMS: "tabular",
    _ACH_TABULAR: "tabular",
    _ACH_NACHA: "nacha",
    _CHECKS_TABULAR: "tabular",
    _CHECKS_X937: "x937",
    _CHECKS_ZIP: "zip",
}


def _move(path: Path, outcome_dir_name: str) -> None:
    """path is .../<kind>/inbox/<filename> -- outcome_dir_name is a sibling of inbox/."""
    target_dir = path.parent.parent / outcome_dir_name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        target = target_dir / f"{stamp}_{path.name}"
    path.rename(target)


def _process_one_file(
    session: Session,
    tenant: Tenant,
    customer_id: uuid.UUID | None,
    customer_number: str | None,
    kind_subpath: str,
    path: Path,
) -> ImportedFileResult:
    settings = get_settings()

    size = path.stat().st_size
    if size > settings.auto_import_max_file_bytes:
        logger.warning(
            "Dropped file %s for tenant %s exceeds auto_import_max_file_bytes (%d > %d) -- moving to failed/ unread",
            path, tenant.slug, size, settings.auto_import_max_file_bytes,
        )
        _move(path, "failed")
        return ImportedFileResult(tenant.slug, customer_number, kind_subpath, path.name, "failed", error="File too large")

    sha256_hex = file_digest(str(path)).hex()
    if bulk_upload_file_service.find_by_content_hash(session, tenant.id, sha256_hex) is not None:
        logger.info(
            "Dropped file %s for tenant %s is a byte-for-byte duplicate of an already-imported file -- skipping, left in place",
            path, tenant.slug,
        )
        return ImportedFileResult(tenant.slug, customer_number, kind_subpath, path.name, "duplicate")

    # The file is stored (and signed) and checked here; its rows are imported by a
    # background job (services/bulk_import_service.py), so a large file doesn't hold up the
    # scan. The counts are known here only when the job has already run (inline mode);
    # otherwise they're on the upload's page when it finishes.
    with path.open("rb") as handle:
        upload_record = bulk_import_service.receive_upload(
            session,
            tenant.id,
            kind=_bulk_upload_kind_for(kind_subpath),
            file_format=_FORMAT_BY_SUBPATH[kind_subpath],
            filename=path.name,
            content_type=None,
            uploaded_by_user_id=None,
            customer_id=customer_id,
            source=BulkUploadSource.AUTO_IMPORT,
            stream=handle,
        )
    if upload_record.status == BulkUploadStatus.REJECTED:
        logger.warning("Failed to import dropped file %s for tenant %s: %s", path, tenant.slug, upload_record.error)
        _move(path, "failed")
        return ImportedFileResult(tenant.slug, customer_number, kind_subpath, path.name, "failed", error=upload_record.error)

    _move(path, "processed")
    jobs_queued(session.get_bind())
    session.refresh(upload_record)
    return ImportedFileResult(
        tenant.slug, customer_number, kind_subpath, path.name, "imported",
        succeeded_count=upload_record.succeeded_count, failed_count=upload_record.failed_count,
    )


def _eligible_files(inbox_dir: Path, *, min_age_seconds: int) -> list[Path]:
    if not inbox_dir.is_dir():
        return []
    now = datetime.now(timezone.utc).timestamp()
    eligible = []
    for entry in sorted(inbox_dir.iterdir()):
        if not entry.is_file():
            continue
        if now - entry.stat().st_mtime < min_age_seconds:
            continue  # still within the write-in-progress guard window
        eligible.append(entry)
    return eligible


def _scan_root(
    session: Session, tenant: Tenant, customer_id: uuid.UUID | None, customer_number: str | None, root: Path
) -> list[ImportedFileResult]:
    settings = get_settings()
    results: list[ImportedFileResult] = []
    for kind_subpath in _ALL_KIND_SUBPATHS:
        inbox_dir = root / kind_subpath / "inbox"
        for path in _eligible_files(inbox_dir, min_age_seconds=settings.auto_import_min_file_age_seconds):
            try:
                results.append(_process_one_file(session, tenant, customer_id, customer_number, kind_subpath, path))
            except Exception:
                logger.exception("Unexpected error importing %s for tenant %s -- leaving file in place", path, tenant.slug)
    return results


def scan_and_import_tenant(session: Session, tenant: Tenant, *, customer_id: uuid.UUID | None = None) -> list[ImportedFileResult]:
    """Scans one tenant's own subtree. If customer_id is given, scans only that
    customer's own subdirectory -- used directly by the on-demand API trigger, which is
    always scoped to the calling tenant (and customer, if customer-scoped) and must
    never see another tenant's or customer's files. With no customer_id, scans the
    tenant-wide directories plus every one of the tenant's customers' own
    subdirectories -- used by the full scheduled/CLI scan."""
    if not _is_safe_path_segment(tenant.slug):
        logger.error("Refusing to scan tenant %s: slug %r is not safe to use as a filesystem path segment", tenant.id, tenant.slug)
        return []

    # Postgres RLS: everything below reads and writes this bank's rows. (Already bound
    # when called from the API; the scheduled scan has no signed-in user.)
    bind_session_to_tenant(session, tenant.id)
    settings = get_settings()
    root = Path(settings.auto_import_dropbox_dir) / tenant.slug
    results: list[ImportedFileResult] = []

    if customer_id is not None:
        customer = session.get(Customer, customer_id)
        if customer is None or customer.tenant_id != tenant.id:
            return results
        if not _is_safe_path_segment(customer.customer_number):
            logger.error(
                "Refusing to scan customer %s (tenant %s): customer_number %r is not safe to use as a filesystem path segment",
                customer.id, tenant.slug, customer.customer_number,
            )
            return results
        return _scan_root(session, tenant, customer_id, customer.customer_number, root / customer.customer_number)

    results += _scan_root(session, tenant, None, None, root)

    customers = session.execute(select(Customer).where(Customer.tenant_id == tenant.id)).scalars().all()
    known_customer_numbers = {c.customer_number for c in customers}
    if root.is_dir():
        for entry in root.iterdir():
            if entry.is_dir() and entry.name not in _TOP_LEVEL_KIND_DIRS and entry.name not in known_customer_numbers:
                logger.warning("Unrecognized customer-number directory %s under tenant %s -- leaving as-is", entry.name, tenant.slug)

    for customer in customers:
        if not _is_safe_path_segment(customer.customer_number):
            logger.error(
                "Skipping customer %s (tenant %s): customer_number %r is not safe to use as a filesystem path segment",
                customer.id, tenant.slug, customer.customer_number,
            )
            continue
        customer_root = root / customer.customer_number
        if customer_root.is_dir():
            results += _scan_root(session, tenant, customer.id, customer.customer_number, customer_root)
    return results


def tenants_with_a_dropbox(session: Session) -> list[Tenant]:
    """Every bank to scan: all of them, once the dropbox root exists. Logs any directory
    under the root that isn't a known bank's slug (it's left alone)."""
    dropbox_root = Path(get_settings().auto_import_dropbox_dir)
    if not dropbox_root.is_dir():
        return []
    tenants = list(session.execute(select(Tenant)).scalars().all())
    known_slugs = {t.slug for t in tenants}
    for entry in dropbox_root.iterdir():
        if entry.is_dir() and entry.name not in known_slugs:
            logger.warning("Unrecognized tenant slug directory %s in %s -- leaving as-is", entry.name, dropbox_root)
    return tenants


def scan_and_import_all_tenants(session: Session) -> list[ImportedFileResult]:
    """The CLI entry point (scripts/import_dropbox.py) -- scans every bank directly, one
    after another. (The scheduler queues one job per bank instead: see
    workers/tasks.py::dropbox_import_job.) One tenant's failure never stops the rest."""
    results: list[ImportedFileResult] = []
    for tenant in tenants_with_a_dropbox(session):
        try:
            results += scan_and_import_tenant(session, tenant)
        except Exception:
            logger.exception("Unexpected error scanning dropbox for tenant %s -- continuing with the rest", tenant.slug)
    return results
