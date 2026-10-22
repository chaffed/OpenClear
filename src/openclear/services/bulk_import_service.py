# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Bulk files imported by a background job (SCALE_PLAN.md S4).

A request (web upload, an API bulk call over the row cap, or a dropbox scan) stores the
file, checks it can be parsed (a malformed file is still rejected whole, before anything
is imported), and queues one `bulk_upload.import` job. The job reads the stored file
again and imports it in chunks of `bulk_import_chunk_rows`:

- each row runs inside a savepoint, so one bad row fails on its own;
- each chunk commits once, together with its row results, audit log entries, the
  records it created (for backing the upload out) and `processed_rows`, so a job that's
  retried after a crash resumes at the first row not yet committed;
- new exceptions don't notify one by one: the upload sends one summary when it's done
  (decision D5).

Files are never read whole into memory to import them: an X9.37 cash letter is read
through an mmap with each check's images sliced out as it's imported, and a zip's
images are read from the archive as each row needs them.
"""

import contextlib
import json
import mmap
import uuid
from decimal import Decimal
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from openclear.bulk_import.nacha import NachaParseError, parse_nacha_file
from openclear.bulk_import.tabular import TabularParseError, parse_tabular_file
from openclear.bulk_import.x937 import X937ParseError, parse_x937_file
from openclear.bulk_import.zip_import import ZipImportError, parse_zip_manifest
from openclear import storage
from openclear.config import get_settings
from openclear.db.account_scope import bind_for_member
from openclear.domain.bulk_upload_file import (
    BulkUploadFile,
    BulkUploadKind,
    BulkUploadRowResult,
    BulkUploadSource,
    BulkUploadStatus,
)
from openclear.domain.exception_item import ExceptionItem
from openclear.services import (
    audit_log_service,
    bulk_upload_file_service,
    bulk_upload_reversal_service,
    notification_service,
    webhook_service,
)

IMPORT_JOB_KIND = "bulk_upload.import"

# The permission that gated an upload also gates seeing it, its results and its file.
PERMISSION_BY_KIND = {
    BulkUploadKind.ISSUED_ITEMS: "issued_item:write",
    BulkUploadKind.ACH_TRANSACTIONS: "ach_transaction:write",
    BulkUploadKind.USERS: "user:manage",
    BulkUploadKind.ACCOUNTS: "account:write",
    BulkUploadKind.CHECK_IMAGES: "check_image:write",
    BulkUploadKind.PAID_ITEMS: "paid_item:write",
}


class BulkFileRejected(Exception):
    """The whole file failed validation; nothing in it is imported."""


@dataclass(frozen=True, slots=True)
class SourceRow:
    label: str
    data: Any


@dataclass(slots=True)
class LoadedFile:
    rows: list[SourceRow]
    # What a row's import may need besides its own data: the zip's images, or the
    # cash letter it reads check images from.
    images: Any = None
    content: Any = None
    _closers: list[Callable[[], None]] = field(default_factory=list)

    def close(self) -> None:
        for closer in reversed(self._closers):
            with contextlib.suppress(Exception):
                closer()


@dataclass(frozen=True, slots=True)
class RowContext:
    session: Session
    tenant_id: uuid.UUID
    scoped_customer_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    options: dict


@dataclass(frozen=True, slots=True)
class ImportSpec:
    load: Callable[[str, str], LoadedFile]  # (stored file's reference, original filename)
    ingest: Callable[[RowContext, SourceRow, LoadedFile], uuid.UUID]
    resource_type: str
    audit_action: str
    audit_noun: str  # "Paid item", for "Paid item created via bulk upload (row 2)"
    creates_check_image: bool = False
    # `load` also takes the upload's options (a layout's settings, for "layout").
    load_takes_options: bool = False


# --- Loading a stored file ----------------------------------------------------------------


def _read(ref: str) -> bytes:
    return storage.read(ref)


def _local_file(ref: str, loaded_closers: list) -> Path:
    """A real file for the stored upload (downloaded first if it's in object storage),
    kept until the LoadedFile is closed."""
    context = storage.local_path(ref)
    path = context.__enter__()
    loaded_closers.append(lambda: context.__exit__(None, None, None))
    return path


def _load_tabular(path: str, filename: str) -> LoadedFile:
    try:
        rows = parse_tabular_file(filename, _read(path))
    except TabularParseError as exc:
        raise BulkFileRejected(str(exc)) from None
    if not rows:
        raise BulkFileRejected("That file has no data rows.")
    return LoadedFile([SourceRow(f"row {index + 2}", row) for index, row in enumerate(rows)])  # +2: 1-based, plus the header


def _load_nacha(path: str, filename: str) -> LoadedFile:
    from openclear.networks.ach.bulk_import import nacha_entry_label

    try:
        entries = parse_nacha_file(_read(path))
    except NachaParseError as exc:
        raise BulkFileRejected(str(exc)) from None
    if not entries:
        raise BulkFileRejected("That file has no entry detail records.")
    return LoadedFile([SourceRow(nacha_entry_label(entry), entry) for entry in entries])


def _load_x937(ref: str, filename: str) -> LoadedFile:
    from openclear.networks.check.bulk_import import x937_item_label

    closers: list = []
    try:
        handle = open(_local_file(ref, closers), "rb")  # noqa: SIM115 -- closed by LoadedFile.close
        closers.append(handle.close)
        content = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) if handle.seek(0, 2) else b""
        if isinstance(content, mmap.mmap):
            closers.append(content.close)
        items = parse_x937_file(content, lazy_images=True)
    except X937ParseError as exc:
        LoadedFile([], _closers=closers).close()
        raise BulkFileRejected(str(exc)) from None
    except Exception:
        LoadedFile([], _closers=closers).close()
        raise
    loaded = LoadedFile([SourceRow(x937_item_label(item), item) for item in items], content=content, _closers=closers)
    if not items:
        loaded.close()
        raise BulkFileRejected("That file has no check detail records.")
    return loaded


def _load_zip(ref: str, filename: str) -> LoadedFile:
    closers: list = []
    try:
        rows, images = parse_zip_manifest(str(_local_file(ref, closers)), lazy_images=True)
    except ZipImportError as exc:
        LoadedFile([], _closers=closers).close()
        raise BulkFileRejected(str(exc)) from None
    except Exception:
        LoadedFile([], _closers=closers).close()
        raise
    archive = getattr(images, "_archive", None)
    if archive is not None:
        closers.append(archive.close)
    loaded = LoadedFile([SourceRow(f"row {index + 2}", row) for index, row in enumerate(rows)], images=images, _closers=closers)
    if not rows:
        loaded.close()
        raise BulkFileRejected("That manifest has no data rows.")
    return loaded


def _load_layout(path: str, filename: str, options: dict) -> LoadedFile:
    """An issued-check file read by the layout the upload chose (bulk_import/layouts.py); the
    upload keeps a copy of the layout's settings, so this always reads it the same way."""
    from openclear.bulk_import import layouts

    settings = ((options or {}).get("layout") or {}).get("settings")
    if not settings:
        raise BulkFileRejected("No layout was chosen for this file.")
    try:
        items = layouts.extract(settings, filename, _read(path))
    except layouts.LayoutError as exc:
        raise BulkFileRejected(str(exc)) from None
    if not items:
        raise BulkFileRejected("The layout found no items in that file.")
    return LoadedFile([SourceRow(label, values) for label, values in items], content=settings)


def _load_json(path: str, filename: str) -> LoadedFile:
    try:
        items = json.loads(_read(path))
    except ValueError as exc:
        raise BulkFileRejected(f"Not valid JSON: {exc}") from None
    if not isinstance(items, list):
        raise BulkFileRejected("Expected a JSON array of items.")
    return LoadedFile([SourceRow(f"row {index}", item) for index, item in enumerate(items)])


# --- Importing one row ---------------------------------------------------------------------


def _create_missing(ctx: RowContext) -> bool:
    return bool(ctx.options.get("create_missing_accounts"))


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in error['loc']) or 'item'}: {error['msg']}" for error in exc.errors())


def _validated(schema, data):
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise ValueError(_validation_message(exc)) from None


def _paid_item_tabular(ctx: RowContext, row: SourceRow, loaded: LoadedFile) -> uuid.UUID:
    from openclear.networks.check.bulk_import import ingest_paid_item_tabular_row

    return ingest_paid_item_tabular_row(
        ctx.session, ctx.tenant_id, row.data, auto_create_accounts=_create_missing(ctx), scoped_customer_id=ctx.scoped_customer_id
    )


def _paid_item_json(ctx: RowContext, row: SourceRow, loaded: LoadedFile) -> uuid.UUID:
    from openclear.domain.paid_item import PaidItemSource
    from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
    from openclear.schemas.paid_item import PaidItemCreate

    payload = _validated(PaidItemCreate, row.data)
    submission = PaidItemSubmission(**payload.model_dump(), source=PaidItemSource.BULK_FILE)
    return ingest_paid_item(ctx.session, ctx.tenant_id, submission, scoped_customer_id=ctx.scoped_customer_id).id


def _issued_item_tabular(ctx: RowContext, row: SourceRow, loaded: LoadedFile) -> uuid.UUID:
    from openclear.services import issued_item_service

    return issued_item_service.create_issued_item_from_row(
        ctx.session, ctx.tenant_id, row.data, submitted_by_user_id=ctx.actor_user_id,
        auto_create_accounts=_create_missing(ctx), scoped_customer_id=ctx.scoped_customer_id,
    )


def _issued_item_layout(ctx: RowContext, row: SourceRow, loaded: LoadedFile) -> uuid.UUID:
    from openclear.bulk_import import layouts
    from openclear.services import issued_item_service

    return issued_item_service.create_issued_item_from_layout_row(
        ctx.session, ctx.tenant_id, layouts.convert(loaded.content, row.data), submitted_by_user_id=ctx.actor_user_id,
        auto_create_accounts=_create_missing(ctx), scoped_customer_id=ctx.scoped_customer_id,
    )


def _issued_item_json(ctx: RowContext, row: SourceRow, loaded: LoadedFile) -> uuid.UUID:
    from openclear.domain.issued_item import IssuedItemSource
    from openclear.schemas.issued_item import IssuedItemCreate
    from openclear.services import issued_item_service

    payload = _validated(IssuedItemCreate, row.data)
    item = issued_item_service.create_issued_item(
        ctx.session, ctx.tenant_id, issued_item_service.IssuedItemInput(**payload.model_dump()),
        submitted_by_user_id=ctx.actor_user_id, source=IssuedItemSource.BULK_FILE, scoped_customer_id=ctx.scoped_customer_id,
    )
    return item.id


def _ach_tabular(ctx: RowContext, row: SourceRow, loaded: LoadedFile) -> uuid.UUID:
    from openclear.networks.ach.bulk_import import ingest_ach_row

    return ingest_ach_row(
        ctx.session, ctx.tenant_id, row.data, auto_create_accounts=_create_missing(ctx), scoped_customer_id=ctx.scoped_customer_id
    )


def _ach_nacha(ctx: RowContext, row: SourceRow, loaded: LoadedFile) -> uuid.UUID:
    from openclear.networks.ach.bulk_import import ingest_nacha_entry

    return ingest_nacha_entry(
        ctx.session, ctx.tenant_id, row.data, auto_create_accounts=_create_missing(ctx), scoped_customer_id=ctx.scoped_customer_id
    )


def _ach_json(ctx: RowContext, row: SourceRow, loaded: LoadedFile) -> uuid.UUID:
    from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
    from openclear.schemas.ach import AchTransactionCreate

    payload = _validated(AchTransactionCreate, row.data)
    return ingest_ach_transaction(
        ctx.session, ctx.tenant_id, AchTransactionSubmission(**payload.model_dump()), scoped_customer_id=ctx.scoped_customer_id
    ).id


def _check_image_zip(ctx: RowContext, row: SourceRow, loaded: LoadedFile) -> uuid.UUID:
    from openclear.networks.check.bulk_import import ingest_check_image_zip_row

    return ingest_check_image_zip_row(ctx.session, ctx.tenant_id, row.data, loaded.images, scoped_customer_id=ctx.scoped_customer_id)


def _check_image_x937(ctx: RowContext, row: SourceRow, loaded: LoadedFile) -> uuid.UUID:
    from openclear.networks.check.bulk_import import ingest_x937_item

    return ingest_x937_item(ctx.session, ctx.tenant_id, row.data, content=loaded.content, scoped_customer_id=ctx.scoped_customer_id)


def _account_tabular(ctx: RowContext, row: SourceRow, loaded: LoadedFile) -> uuid.UUID:
    from openclear.services import account_service

    return account_service.create_account_from_row(ctx.session, ctx.tenant_id, row.data, scoped_customer_id=ctx.scoped_customer_id)


_PAID = dict(resource_type="paid_item", audit_action="paid_item.create", audit_noun="Paid item")
_ISSUED = dict(resource_type="issued_item", audit_action="issued_item.create", audit_noun="Issued item")
_ACH = dict(resource_type="ach_transaction", audit_action="ach_transaction.create", audit_noun="ACH transaction")
_CHECK_IMAGE = dict(
    resource_type="paid_item", audit_action="check_image.upload", audit_noun="Check image + paid item", creates_check_image=True
)

BUILTIN_SPECS: dict[tuple[BulkUploadKind, str], ImportSpec] = {
    (BulkUploadKind.PAID_ITEMS, "tabular"): ImportSpec(_load_tabular, _paid_item_tabular, **_PAID),
    (BulkUploadKind.PAID_ITEMS, "json"): ImportSpec(_load_json, _paid_item_json, **_PAID),
    (BulkUploadKind.ISSUED_ITEMS, "tabular"): ImportSpec(_load_tabular, _issued_item_tabular, **_ISSUED),
    (BulkUploadKind.ISSUED_ITEMS, "json"): ImportSpec(_load_json, _issued_item_json, **_ISSUED),
    (BulkUploadKind.ISSUED_ITEMS, "layout"): ImportSpec(_load_layout, _issued_item_layout, load_takes_options=True, **_ISSUED),
    (BulkUploadKind.ACH_TRANSACTIONS, "tabular"): ImportSpec(_load_tabular, _ach_tabular, **_ACH),
    (BulkUploadKind.ACH_TRANSACTIONS, "nacha"): ImportSpec(_load_nacha, _ach_nacha, **_ACH),
    (BulkUploadKind.ACH_TRANSACTIONS, "json"): ImportSpec(_load_json, _ach_json, **_ACH),
    (BulkUploadKind.CHECK_IMAGES, "zip"): ImportSpec(_load_zip, _check_image_zip, **_CHECK_IMAGE),
    (BulkUploadKind.CHECK_IMAGES, "x937"): ImportSpec(_load_x937, _check_image_x937, **_CHECK_IMAGE),
    (BulkUploadKind.ACCOUNTS, "tabular"): ImportSpec(
        _load_tabular, _account_tabular, resource_type="account", audit_action="account.create", audit_noun="Account"
    ),
}


@contextlib.contextmanager
def _loaded(spec: ImportSpec, record: BulkUploadFile) -> Iterator[LoadedFile]:
    if spec.load_takes_options:
        loaded = spec.load(record.storage_path, record.original_filename, record.options or {})
    else:
        loaded = spec.load(record.storage_path, record.original_filename)
    try:
        yield loaded
    finally:
        loaded.close()


def spec_for(kind: BulkUploadKind, file_format: str) -> ImportSpec:
    """The import format, built in (BUILTIN_SPECS) or from a plugin (plugins/__init__.py)."""
    from openclear import plugins

    return plugins.lookup("import_formats", (kind, file_format), what="import format")


def _spec(record: BulkUploadFile) -> ImportSpec:
    return spec_for(record.kind, (record.options or {}).get("format", "tabular"))


# --- Receiving a file ----------------------------------------------------------------------


def receive_api_items(
    session: Session, tenant_id: uuid.UUID, *, kind: BulkUploadKind, items: list, user_id: uuid.UUID | None, customer_id: uuid.UUID | None
) -> BulkUploadFile:
    """A JSON API bulk call over `bulk_api_sync_max_rows`: the validated items are stored as
    the upload's file and imported by the job like any other."""
    data = json.dumps([item.model_dump(mode="json") for item in items]).encode()
    return receive_upload(
        session, tenant_id, kind=kind, file_format="json", filename=f"api-{kind.value}.json", content_type="application/json",
        uploaded_by_user_id=user_id, customer_id=customer_id, source=BulkUploadSource.API, data=data,
    )


def receive_upload(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    kind: BulkUploadKind,
    file_format: str,
    filename: str,
    content_type: str | None,
    uploaded_by_user_id: uuid.UUID | None,
    customer_id: uuid.UUID | None,
    source: BulkUploadSource = BulkUploadSource.MANUAL,
    stream=None,
    data: bytes | None = None,
    options: dict | None = None,
) -> BulkUploadFile:
    """Stores and signs the file, then either rejects it (status REJECTED, with `error`)
    or queues its import (status QUEUED). Commits either way, so a rejected file is still
    kept as evidence of what was sent. Call workers.jobs.jobs_queued afterwards. Raises
    ValueError (nothing stored) for a format that doesn't exist or isn't on for the bank."""
    from openclear.services import plugin_service
    from openclear.workers.jobs import enqueue

    # A plugin's format only for a bank that turned it on; raises before the file is stored.
    plugin_service.require_enabled(session, tenant_id, "import_formats", (kind, file_format), what="import format")
    record = bulk_upload_file_service.record_uploaded_file(
        session, tenant_id, kind=kind, filename=filename, content_type=content_type, data=data, stream=stream,
        uploaded_by_user_id=uploaded_by_user_id, customer_id=customer_id, source=source,
    )
    record.options = {**(options or {}), "format": file_format}
    spec = spec_for(kind, file_format)
    try:
        with _loaded(spec, record) as loaded:
            record.total_rows = len(loaded.rows)
    except BulkFileRejected as exc:
        record.status, record.error, record.completed_at = BulkUploadStatus.REJECTED, str(exc), datetime.now(timezone.utc)
        session.commit()
        return record
    record.status = BulkUploadStatus.QUEUED
    enqueue(session, IMPORT_JOB_KIND, tenant_id=tenant_id, payload={"upload_id": str(record.id)}, dedupe_key=f"bulk-upload:{record.id}")
    session.commit()
    return record


# --- The job ---------------------------------------------------------------------------------


def run_import(session: Session, upload_id: uuid.UUID, *, last_attempt: bool = False) -> None:
    """The `bulk_upload.import` job: imports the rows not yet committed. On the job's last
    attempt a failure also marks the upload FAILED, so the page stops showing progress."""
    record = session.get(BulkUploadFile, upload_id)
    if record is None or record.status in (BulkUploadStatus.COMPLETED, BulkUploadStatus.REJECTED):
        return
    if record.quarantined_at is not None:  # found to be malware after it arrived (services/malware_service.py)
        record.status, record.error = BulkUploadStatus.REJECTED, "Quarantined: identified as malware after it was uploaded."
        session.commit()
        return
    # Only the accounts the uploader may see (db/account_scope.py).
    bind_for_member(session, record.tenant_id, record.uploaded_by_user_id, record.customer_id)
    from openclear.workers.jobs import LeaseLost

    try:
        _import(session, record)
    except LeaseLost:
        session.rollback()  # another worker has the job now and carries on from processed_rows
        return
    except Exception as exc:
        session.rollback()
        if last_attempt:
            record = session.get(BulkUploadFile, upload_id)
            record.status, record.error, record.completed_at = BulkUploadStatus.FAILED, str(exc)[:2000], datetime.now(timezone.utc)
            webhook_service.emit(session, record.tenant_id, "bulk_upload.completed", {"upload_id": record.id, "status": "failed"})
            session.commit()
        raise


def _reconcile(session: Session, record: BulkUploadFile, rows: list) -> None:
    """For a file with control records (X9.37, NACHA), keep its declared items and total and what
    was loaded (CONTROLS_PLAN.md CT3; the file_reconciliation check). The parsers already refuse a
    file whose own control records don't add up, so a difference is rows refused on the way in."""
    from openclear.bulk_import.nacha import NachaEntry
    from openclear.bulk_import.x937 import X937CheckItem

    items = [getattr(r, "data", r) for r in rows]
    if not items or not all(isinstance(i, (X937CheckItem, NachaEntry)) for i in items):
        return
    loaded = set(session.execute(select(BulkUploadRowResult.row_index).where(
        BulkUploadRowResult.bulk_upload_file_id == record.id, BulkUploadRowResult.success.is_(True))).scalars())
    record.control_item_count, record.control_total = len(items), sum((i.amount for i in items), Decimal("0"))
    record.loaded_item_count = len(loaded)
    record.loaded_total = sum((items[n].amount for n in loaded if n < len(items)), Decimal("0"))


def _import(session: Session, record: BulkUploadFile) -> None:
    from openclear.workers.jobs import check_lease, jobs_queued

    spec = _spec(record)
    if record.status != BulkUploadStatus.PROCESSING:
        record.status, record.started_at = BulkUploadStatus.PROCESSING, datetime.now(timezone.utc)
        session.commit()
    ctx = RowContext(session, record.tenant_id, record.customer_id, record.uploaded_by_user_id, record.options or {})
    chunk_rows = max(1, get_settings().bulk_import_chunk_rows)
    queued_ocr = False

    with _loaded(spec, record) as loaded, notification_service.exceptions_summarized():
        rows = loaded.rows
        record.total_rows = len(rows)
        start = record.processed_rows or 0
        while start < len(rows):
            chunk = rows[start : start + chunk_rows]
            # Open the chunk's transaction with a write. SQLite's driver doesn't begin a
            # transaction until the first write, and a SAVEPOINT outside one commits when
            # it's released, so without this each row would commit on its own and a crash
            # mid-chunk would leave rows the retry imports again.
            session.execute(update(BulkUploadFile).where(BulkUploadFile.id == record.id).values(processed_rows=start))
            outcomes = []
            for offset, row in enumerate(chunk):
                savepoint = session.begin_nested()
                try:
                    created_id = spec.ingest(ctx, row, loaded)
                    savepoint.commit()
                    outcomes.append((start + offset, row, created_id, None, None))
                except Exception as exc:  # noqa: BLE001 -- one bad row fails on its own
                    savepoint.rollback()
                    outcomes.append((start + offset, row, None, str(exc) or exc.__class__.__name__, getattr(exc, "field", None)))
            queued_ocr |= _record_chunk(session, record, spec, outcomes)
            start += len(chunk)
            record.processed_rows = start
            check_lease(session)  # this worker still owns the job, or the chunk isn't committed
            session.commit()
        _reconcile(session, record, rows)

    _send_summary(session, record)
    record.status, record.completed_at = BulkUploadStatus.COMPLETED, datetime.now(timezone.utc)
    webhook_service.emit(session, record.tenant_id, "bulk_upload.completed", {
        "upload_id": record.id, "status": record.status.value, "succeeded": record.succeeded_count, "failed": record.failed_count,
    })
    session.commit()
    if queued_ocr:
        jobs_queued(session.get_bind())


def _channel(record: BulkUploadFile) -> str | None:
    """The audit channel for what an upload creates; None for a dropbox file, which has no
    user behind it and isn't audited per row (as before S4)."""
    return {BulkUploadSource.MANUAL: "web", BulkUploadSource.API: "api"}.get(record.source)


def _record_chunk(session: Session, record: BulkUploadFile, spec: ImportSpec, outcomes: list) -> bool:
    """Everything that goes with a chunk's rows, in the same transaction. Done after the
    rows themselves, so the audit log's per-bank lock is held only briefly."""
    from openclear.networks.check.ocr_processing import queue_ocr
    from openclear.repositories.check_image_repo import CheckImageRepository

    channel = _channel(record)
    created_ids = []
    audit_actions = []
    queued_ocr = False
    for row_index, row, created_id, error, error_field in outcomes:
        session.add(BulkUploadRowResult(
            tenant_id=record.tenant_id, bulk_upload_file_id=record.id, row_index=row_index, row_label=row.label[:200],
            success=created_id is not None, created_id=created_id, error=error, error_field=(error_field or None) and error_field[:60],
        ))
        if created_id is None:
            continue
        created_ids.append(created_id)
        if channel is not None:
            via = "bulk upload" if channel == "web" else "bulk API"
            audit_actions.append(audit_log_service.PendingAction(
                actor_user_id=record.uploaded_by_user_id, channel=channel, action=spec.audit_action,
                summary=f"{spec.audit_noun} created via {via} ({row.label})", resource_type=spec.resource_type, resource_id=created_id,
            ))
        bulk_upload_reversal_service.track_created_record(
            session, record.tenant_id, record.id, resource_type=spec.resource_type, resource_id=created_id, row_label=row.label[:100]
        )
        if spec.creates_check_image:
            images = CheckImageRepository(session, record.tenant_id, record.customer_id).list(paid_item_id=created_id)
            if images:
                queue_ocr(session, record.tenant_id, images[0].id)
                queued_ocr = True
                bulk_upload_reversal_service.track_created_record(
                    session, record.tenant_id, record.id, resource_type="check_image", resource_id=images[0].id,
                    row_label=row.label[:100],
                )

    # Last, so the bank's audit lock is held only until this chunk commits.
    audit_log_service.record_actions(session, record.tenant_id, audit_actions)

    succeeded = len(created_ids)
    record.succeeded_count = (record.succeeded_count or 0) + succeeded
    record.failed_count = (record.failed_count or 0) + len(outcomes) - succeeded
    if created_ids and spec.resource_type in ("paid_item", "ach_transaction"):
        from openclear.domain.ach_transaction import AchTransaction
        from openclear.domain.paid_item import PaidItem

        source = PaidItem if spec.resource_type == "paid_item" else AchTransaction
        counts = dict(record.exception_counts or {})
        # By customer and account, so a person who sees only some accounts is told only of theirs.
        for customer_id, account_id in session.execute(
            select(ExceptionItem.customer_id, source.account_id).join(source, source.id == ExceptionItem.source_item_id).where(
                ExceptionItem.tenant_id == record.tenant_id, ExceptionItem.source_item_id.in_(created_ids)
            )
        ):
            key = f"{customer_id or ''}:{account_id}"
            counts[key] = counts.get(key, 0) + 1
        record.exception_counts = counts  # reassigned, so the JSON column is saved
    session.flush()
    return queued_ocr


def _send_summary(session: Session, record: BulkUploadFile) -> None:
    if record.exception_counts:
        notification_service.notify_exceptions_created_summary(
            session, record.tenant_id, record.exception_counts, source_label=f"the file {record.original_filename}"
        )


# --- Reading results ---------------------------------------------------------------------------


def row_results(
    session: Session, upload: BulkUploadFile, *, failures_only: bool = False, limit: int | None = None, offset: int = 0
) -> list[BulkUploadRowResult]:
    query = select(BulkUploadRowResult).where(
        BulkUploadRowResult.tenant_id == upload.tenant_id, BulkUploadRowResult.bulk_upload_file_id == upload.id
    )
    if failures_only:
        query = query.where(BulkUploadRowResult.success.is_(False))
    query = query.order_by(BulkUploadRowResult.row_index).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return list(session.execute(query).scalars())


def check_job(session: Session, upload: BulkUploadFile) -> BulkUploadFile:
    """Marks an upload FAILED if its job gave up without the handler getting to (a worker
    that died on the last attempt is failed by the queue itself). Called when the upload is
    looked at, so a page never shows "importing" for an import that's over."""
    from openclear.domain.job import Job, JobStatus

    if upload.status not in (BulkUploadStatus.QUEUED, BulkUploadStatus.PROCESSING):
        return upload
    job = session.execute(
        select(Job).where(Job.dedupe_key == f"bulk-upload:{upload.id}").order_by(Job.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if job is not None and job.status == JobStatus.FAILED:
        upload.status, upload.completed_at = BulkUploadStatus.FAILED, datetime.now(timezone.utc)
        upload.error = upload.error or "The import stopped and wasn't retried; see the platform's failed jobs."
        session.commit()
    return upload


def is_finished(upload: BulkUploadFile) -> bool:
    return upload.status in (BulkUploadStatus.COMPLETED, BulkUploadStatus.REJECTED, BulkUploadStatus.FAILED)


# --- Fixing failed rows in the browser (UX_PLAN.md U2) ----------------------------------------

# Formats whose rows are a set of named values a person can edit; a cash letter, a NACHA
# file or a zip of images isn't fixed this way.
CORRECTABLE_FORMATS = frozenset({"tabular", "layout", "json"})
MAX_FIXES_SHOWN = 50


@dataclass
class FailedRow:
    row_index: int
    label: str
    error: str | None
    error_field: str | None
    values: dict[str, str]


def correctable(record: BulkUploadFile) -> bool:
    return (record.status == BulkUploadStatus.COMPLETED and record.backed_out_at is None and bool(record.failed_count)
            and (record.options or {}).get("format", "tabular") in CORRECTABLE_FORMATS and record.kind != BulkUploadKind.USERS)


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _fixes(session: Session, record: BulkUploadFile):
    from openclear.domain.bulk_upload_file import BulkUploadCorrection

    return list(session.execute(select(BulkUploadCorrection).where(
        BulkUploadCorrection.tenant_id == record.tenant_id, BulkUploadCorrection.bulk_upload_file_id == record.id,
    ).order_by(BulkUploadCorrection.corrected_at)).scalars())


def corrections(session: Session, record: BulkUploadFile) -> list:
    return _fixes(session, record)


def open_failures(session: Session, record: BulkUploadFile, *, limit: int = MAX_FIXES_SHOWN) -> list[FailedRow]:
    """The failed rows not yet fixed, with what was last tried for each: the file's values,
    or the last attempt at a fix and its error."""
    fixes = _fixes(session, record)
    fixed = {f.row_index for f in fixes if f.success}
    latest = {f.row_index: f for f in fixes if not f.success}
    failures = [r for r in row_results(session, record, failures_only=True) if r.row_index not in fixed][:limit]
    if not failures:
        return []
    with _loaded(_spec(record), record) as loaded:
        rows = []
        for result in failures:
            if result.row_index >= len(loaded.rows) or not isinstance(loaded.rows[result.row_index].data, dict):
                continue
            original = loaded.rows[result.row_index].data
            attempt = latest.get(result.row_index)
            values = {str(k): _as_text(v) for k, v in (attempt.data if attempt else original).items()}
            rows.append(FailedRow(result.row_index, result.row_label, attempt.error if attempt else result.error,
                                  attempt.error_field if attempt else result.error_field, values))
    return rows


def apply_corrections(session: Session, record: BulkUploadFile, edits: dict[int, dict[str, str]], *, user_id: uuid.UUID | None,
                      channel: str = "web") -> tuple[int, int]:
    """Imports the edited rows, each on its own, and records each attempt. Only rows that
    failed and aren't fixed yet, and only the fields the row already had. Returns (fixed,
    still failing). The caller commits."""
    from openclear.domain.bulk_upload_file import BulkUploadCorrection

    if not correctable(record):
        raise ValueError("This upload's rows can't be fixed here.")
    spec = _spec(record)
    fixes = _fixes(session, record)
    already = {f.row_index for f in fixes if f.success}
    failed = {r.row_index for r in row_results(session, record, failures_only=True)} - already
    ctx = RowContext(session, record.tenant_id, record.customer_id, user_id, record.options or {})
    fixed = still = 0
    audit = []
    with _loaded(spec, record) as loaded:
        for row_index, values in sorted(edits.items())[:MAX_FIXES_SHOWN]:
            if row_index not in failed or row_index >= len(loaded.rows):
                continue
            original = loaded.rows[row_index]
            if not isinstance(original.data, dict):
                continue
            data = {key: (values[str(key)].strip() if str(key) in values else value) for key, value in original.data.items()}
            savepoint = session.begin_nested()
            try:
                created_id = spec.ingest(ctx, SourceRow(original.label, data), loaded)
                savepoint.commit()
            except Exception as exc:  # noqa: BLE001 -- shown beside the row, like the import's own errors
                savepoint.rollback()
                session.add(BulkUploadCorrection(
                    tenant_id=record.tenant_id, bulk_upload_file_id=record.id, row_index=row_index, data=data, success=False,
                    error=str(exc) or exc.__class__.__name__, error_field=(getattr(exc, "field", None) or None),
                    corrected_by_user_id=user_id))
                still += 1
                continue
            session.add(BulkUploadCorrection(
                tenant_id=record.tenant_id, bulk_upload_file_id=record.id, row_index=row_index, data=data, success=True,
                created_id=created_id, corrected_by_user_id=user_id))
            bulk_upload_reversal_service.track_created_record(
                session, record.tenant_id, record.id, resource_type=spec.resource_type, resource_id=created_id,
                row_label=f"{original.label} (fixed)"[:100])
            audit.append(audit_log_service.PendingAction(
                actor_user_id=user_id, channel=channel, action=spec.audit_action,
                summary=f"{spec.audit_noun} created by fixing a failed row of {record.original_filename} ({original.label})",
                resource_type=spec.resource_type, resource_id=created_id))
            fixed += 1
    audit_log_service.record_actions(session, record.tenant_id, audit)
    session.flush()
    return fixed, still
