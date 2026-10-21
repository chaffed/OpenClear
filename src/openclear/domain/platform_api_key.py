# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class PlatformApiKey(Base):
    """A cross-tenant, read-only credential for the usage-metering API
    (api/v1/platform_usage.py) — the one credential type in this app that isn't scoped to
    a single tenant, since an external billing/account-analysis system needs usage counts
    across every tenant in one pass. Deliberately has no tenant_id column at all and
    carries no other privilege; nothing but the usage endpoints ever accepts it.

    `key_hash` is a plain sha256 hex digest, not a slow password hash (auth/security.py's
    bcrypt) — the raw key is a high-entropy random token (services/platform_api_key_
    service.py::generate_and_create), not a low-entropy human password, so a fast
    cryptographic hash is the right tool and avoids needless latency on every request.
    The raw key itself is never stored anywhere; only its hash, and only ever shown to
    the caller once, at creation time."""

    __tablename__ = "platform_api_key"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # One-way, same pattern as every other revoke in this app (AchAuthorizationRule.status,
    # BulkUploadFile.backed_out_at, ...) — nothing supports un-revoking a key.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # What the key may do: "usage" (the metering API; every key created before scopes
    # existed), "operations" (jobs, metrics, security events) and "billing". Checked by
    # auth/platform_api_key_deps.py.
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=lambda: ["usage"])
