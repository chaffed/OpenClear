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

from openclear.bulk_import.nacha import parse_nacha_file
from openclear.bulk_import.signing import content_hash
from openclear.bulk_import.tabular import parse_tabular_file
from openclear.bulk_import.x937 import parse_x937_file
from openclear.bulk_import.zip_import import parse_zip_manifest
from openclear.config import get_settings
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.bulk_upload_file import BulkUploadKind, BulkUploadSource
from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant
from openclear.networks.ach.bulk_import import ingest_ach_rows, ingest_nacha_entries
from openclear.networks.check.bulk_import import ingest_check_image_zip_rows, ingest_paid_item_tabular_rows, ingest_x937_items
from openclear.services import bulk_upload_file_service, issued_item_service

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


def _ingest(
    session: Session, tenant_id: uuid.UUID, kind_subpath: str, filename: str, content: bytes, *, scoped_customer_id: uuid.UUID | None
) -> tuple[int, int]:
    """Parses + ingests one dropped file's bytes according to which subdirectory it
    came from. Returns (succeeded_count, failed_count) for a file that at least parsed.
    Raises (a format-specific ParseError, or plain ValueError for an empty file) on a
    whole-file failure -- the caller moves the file to failed/ in that case."""
    if kind_subpath == _ISSUED_ITEMS:
        rows = parse_tabular_file(filename, content)
        if not rows:
            raise ValueError("File has no data rows")
        results = issued_item_service.create_issued_items_from_rows(
            session, tenant_id, rows, submitted_by_user_id=None, scoped_customer_id=scoped_customer_id
        )
    elif kind_subpath == _ACH_TABULAR:
        rows = parse_tabular_file(filename, content)
        if not rows:
            raise ValueError("File has no data rows")
        results = ingest_ach_rows(session, tenant_id, rows, scoped_customer_id=scoped_customer_id)
    elif kind_subpath == _ACH_NACHA:
        entries = parse_nacha_file(content)
        if not entries:
            raise ValueError("File has no entry detail records")
        results = ingest_nacha_entries(session, tenant_id, entries, scoped_customer_id=scoped_customer_id)
    elif kind_subpath == _CHECKS_TABULAR:
        rows = parse_tabular_file(filename, content)
        if not rows:
            raise ValueError("File has no data rows")
        results = ingest_paid_item_tabular_rows(session, tenant_id, rows, scoped_customer_id=scoped_customer_id)
    elif kind_subpath == _CHECKS_X937:
        items = parse_x937_file(content)
        if not items:
            raise ValueError("File has no check detail records")
        results = ingest_x937_items(session, tenant_id, items, scoped_customer_id=scoped_customer_id)
    else:  # _CHECKS_ZIP
        rows, images = parse_zip_manifest(content)
        if not rows:
            raise ValueError("Manifest has no data rows")
        results = ingest_check_image_zip_rows(session, tenant_id, rows, images, scoped_customer_id=scoped_customer_id)

    return sum(r.success for r in results), sum(not r.success for r in results)


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

    data = path.read_bytes()
    sha256_hex = content_hash(data)
    if bulk_upload_file_service.find_by_content_hash(session, tenant.id, sha256_hex) is not None:
        logger.info(
            "Dropped file %s for tenant %s is a byte-for-byte duplicate of an already-imported file -- skipping, left in place",
            path, tenant.slug,
        )
        return ImportedFileResult(tenant.slug, customer_number, kind_subpath, path.name, "duplicate")

    upload_record = bulk_upload_file_service.record_uploaded_file(
        session,
        tenant.id,
        kind=_bulk_upload_kind_for(kind_subpath),
        filename=path.name,
        content_type=None,
        data=data,
        uploaded_by_user_id=None,
        customer_id=customer_id,
        source=BulkUploadSource.AUTO_IMPORT,
    )
    session.commit()

    try:
        succeeded_count, failed_count = _ingest(session, tenant.id, kind_subpath, path.name, data, scoped_customer_id=customer_id)
    except Exception as exc:  # noqa: BLE001 -- a malformed dropped file must never crash the scan
        session.rollback()
        logger.warning("Failed to import dropped file %s for tenant %s: %s", path, tenant.slug, exc)
        _move(path, "failed")
        return ImportedFileResult(tenant.slug, customer_number, kind_subpath, path.name, "failed", error=str(exc))

    bulk_upload_file_service.set_result_counts(session, upload_record, succeeded_count=succeeded_count, failed_count=failed_count)
    session.commit()
    _move(path, "processed")
    return ImportedFileResult(
        tenant.slug, customer_number, kind_subpath, path.name, "imported",
        succeeded_count=succeeded_count, failed_count=failed_count,
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


def scan_and_import_all_tenants(session: Session) -> list[ImportedFileResult]:
    """The in-app scheduler / CLI entry point -- iterates every tenant's own subtree.
    One tenant's failure (a missing/unreadable directory, an unexpected exception)
    never stops the rest of the scan, same isolation principle as
    workers/tasks.py::_train_and_log."""
    settings = get_settings()
    dropbox_root = Path(settings.auto_import_dropbox_dir)
    if not dropbox_root.is_dir():
        return []

    tenants = session.execute(select(Tenant)).scalars().all()
    known_slugs = {t.slug for t in tenants}
    for entry in dropbox_root.iterdir():
        if entry.is_dir() and entry.name not in known_slugs:
            logger.warning("Unrecognized tenant slug directory %s in %s -- leaving as-is", entry.name, dropbox_root)

    results: list[ImportedFileResult] = []
    for tenant in tenants:
        try:
            results += scan_and_import_tenant(session, tenant)
        except Exception:
            logger.exception("Unexpected error scanning dropbox for tenant %s -- continuing with the rest", tenant.slug)
    return results
