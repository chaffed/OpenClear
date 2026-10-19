# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class BulkUploadKind(str, enum.Enum):
    ISSUED_ITEMS = "issued_items"
    ACH_TRANSACTIONS = "ach_transactions"
    USERS = "users"
    ACCOUNTS = "accounts"
    CHECK_IMAGES = "check_images"
    # Checks presented for payment as plain delimited data, with no image attached —
    # distinct from CHECK_IMAGES, which always implies one. See
    # networks/check/bulk_import.py::ingest_paid_item_tabular_rows.
    PAID_ITEMS = "paid_items"
    # Known-fraud training labels (check and ACH both land under this one kind) — see
    # services/fraud_training_service.py.
    FRAUD_TRAINING_EXAMPLES = "fraud_training_examples"


class BulkUploadSource(str, enum.Enum):
    MANUAL = "manual"
    # Landed by services/dropbox_import_service.py, not a human clicking "Bulk upload" —
    # see that module for the drop-directory layout. Mirrors AchTransactionSource's own
    # API/BULK_FILE/MANUAL shape for the same "how did this get here" question.
    AUTO_IMPORT = "auto_import"
    # A JSON API bulk request too large to run inside the request (SCALE_PLAN.md S4): its
    # body is stored as the file and imported by a background job.
    API = "api"


class BulkUploadStatus(str, enum.Enum):
    """Where a file is in its import. Files imported by a background job
    (services/bulk_import_service.py) go RECEIVED -> QUEUED -> PROCESSING -> COMPLETED, or
    REJECTED (the whole file failed validation, nothing imported) or FAILED (the job gave
    up; rows already committed stay). The uploads still processed inside the request
    (users, fraud-training labels) go straight from RECEIVED to COMPLETED."""

    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class BulkUploadFile(Base):
    """The original bytes of every bulk file submitted (issued items, ACH, users,
    checks — see services/bulk_upload_file_service.py), whether submitted manually or
    landed by the auto-import dropbox watcher (`source`), kept for audit purposes even
    when the file is rejected outright: a malformed submission is still evidence of what
    was actually sent. `sha256_hex` is a plain content fingerprint anyone can quote
    independently; `signature_hex` is the actual tamper-evidence, an ECDSA-P256/SHA256
    signature computed with a server-held private key (bulk_import/signing.py) —
    re-verified against the file on disk on demand, not just trusted as a stored flag."""

    __tablename__ = "bulk_upload_file"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    # NULL = the uploader was tenant-wide staff (a single file can legitimately span
    # several customers, e.g. a bank-wide admin's CSV); a real value means it was
    # uploaded within that one customer's scope — set from the uploader's own
    # ctx.customer_id at upload time (services/bulk_upload_file_service.py).
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    kind: Mapped[BulkUploadKind] = mapped_column(Enum(BulkUploadKind, name="bulk_upload_kind", native_enum=False, length=30), nullable=False)
    source: Mapped[BulkUploadSource] = mapped_column(
        Enum(BulkUploadSource, name="bulk_upload_source", native_enum=False, length=20),
        nullable=False,
        default=BulkUploadSource.MANUAL,
    )

    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    # Widened from String(64) (an HMAC-SHA256 hex digest) to fit a hex-encoded ECDSA
    # P-256 DER signature, which can run up to ~144 hex chars.
    signature_hex: Mapped[str] = mapped_column(String(200), nullable=False)

    succeeded_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The background import's progress (SCALE_PLAN.md S4). `processed_rows` is committed
    # with each chunk's rows, so a job that's retried resumes where it stopped. `options`
    # holds what the uploader chose (format, create missing accounts). `exception_counts`
    # counts new exceptions per customer ("" for none), for the one summary notification.
    status: Mapped[BulkUploadStatus] = mapped_column(
        Enum(BulkUploadStatus, name="bulk_upload_status", native_enum=False, length=20),
        nullable=False,
        default=BulkUploadStatus.RECEIVED,
    )
    total_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    options: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    exception_counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # A presented-items file with control records (X9.37, NACHA; CONTROLS_PLAN.md CT3): its items and
    # total as the file declares them (the parser already refuses a file whose own controls don't
    # add up), and what was loaded. They differ when rows were refused on the way in.
    control_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    control_total: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    loaded_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loaded_total: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Nullable — an auto-imported file (source == AUTO_IMPORT) has no human uploader,
    # same "nullable means no attributable actor" shape as Decision.submitted_by_user_id.
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Set together, once, by services/bulk_upload_reversal_service.py::back_out_upload —
    # marks the whole upload as backed out. One-way: nothing in this app supports
    # "undo the undo", matching every other void/cancel/revoke action's own one-way
    # design, so a backout attempt is refused once these are set.
    backed_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Found to be malware after it was accepted (services/malware_service.py; OW-D34): never
    # served or processed again.
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backed_out_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)


class BulkUploadRowResult(Base):
    """One row's outcome from a background import: what the results page and the API's
    status endpoint show. Written in the same transaction as the rows it describes."""

    __tablename__ = "bulk_upload_row_result"
    __table_args__ = (UniqueConstraint("bulk_upload_file_id", "row_index"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    bulk_upload_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bulk_upload_file.id"), nullable=False)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-based position in the file
    row_label: Mapped[str] = mapped_column(String(200), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The field a failed row's error is about ("amount"), so the fix form can show it beside
    # that field (bulk_import/fields.RowFieldError.field). Null when it's about the row.
    error_field: Mapped[str | None] = mapped_column(String(60), nullable=True)


class BulkUploadCorrection(Base):
    """A failed row fixed in the browser and imported again (UX_PLAN.md U2). The uploaded
    file stays as received (it's signed); each fix is its own record, with who made it,
    what they submitted and what happened. What it creates is tracked with the upload's
    other records, so backing the upload out covers it too."""

    __tablename__ = "bulk_upload_correction"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    bulk_upload_file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bulk_upload_file.id"), nullable=False, index=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_field: Mapped[str | None] = mapped_column(String(60), nullable=True)
    corrected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    corrected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
