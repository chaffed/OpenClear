# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
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

    # Nullable — an auto-imported file (source == AUTO_IMPORT) has no human uploader,
    # same "nullable means no attributable actor" shape as Decision.submitted_by_user_id.
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Set together, once, by services/bulk_upload_reversal_service.py::back_out_upload —
    # marks the whole upload as backed out. One-way: nothing in this app supports
    # "undo the undo", matching every other void/cancel/revoke action's own one-way
    # design, so a backout attempt is refused once these are set.
    backed_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backed_out_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
