# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class WsudStatement(Base):
    """A signed Written Statement of Unauthorized Debit — see services/wsud_service.py.
    Always customer-scoped (`customer_id` non-nullable): in OpenClear the "customer" is the
    actual account holder, so this is a self-service attestation by that customer's own
    login, never a bank employee signing on their behalf. Covers one or more disputed
    ACH transactions (see WsudStatementTransaction) with a single signature.

    Tamper-evident like the audit log (services/audit_log_service.py) — `signature_hex`
    is an ECDSA-P256/SHA256 signature over this row's own canonical fields, using a key
    pair (`wsud_signing_private_key_path`) dedicated to this artifact type alone, same
    "a leaked key for one signed-artifact type shouldn't compromise another" reasoning
    as the JWT/file-signing/audit-log keys. Unlike the audit log, this is a standalone
    per-document signature, not a hash chain — a WSUD statement doesn't depend on
    "what came before it" the way a sequential action log does.

    `statement_text_snapshot`/`consent_disclosure_version` capture exactly what was
    shown to and signed by this customer at this moment — if the disclosure/attestation
    text is edited later, past signatures still mean exactly what they meant when
    signed, same "snapshot as observed" philosophy as Decision.features_json.

    NOTE: the disclosure/attestation text this signs over
    (config.py's wsud_consent_disclosure_text/wsud_attestation_text settings) defaults to
    placeholder legal language, not reviewed by counsel — see that module's docstring and
    services/wsud_service.py's."""

    __tablename__ = "wsud_statement"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer.id"), nullable=False, index=True)

    signed_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    signer_typed_name: Mapped[str] = mapped_column(String(255), nullable=False)
    signer_ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signer_user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    consent_disclosure_version: Mapped[str] = mapped_column(String(20), nullable=False)
    statement_text_snapshot: Mapped[str] = mapped_column(Text, nullable=False)

    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Widened to fit a hex-encoded ECDSA P-256 DER signature (up to ~144 hex chars),
    # same sizing reasoning as AuditLogEntry.entry_hash.
    signature_hex: Mapped[str] = mapped_column(String(200), nullable=False)
