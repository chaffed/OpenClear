# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class CheckReturnReason(Base):
    """A bank's list of reasons for returning a check (UX_PLAN.md U4, decision U-D3), like
    `ach_return_reason` for ACH. Returning a check from a web page means choosing one of
    these; the API still accepts free text. `return_code` is the return reason code a
    return file to the core carries (ANSI X9.100-188, e.g. "N" for an altered or fictitious
    item); a bank sets its own where its core differs. Never deleted: retired with
    `is_active`, since past decisions name them."""

    __tablename__ = "check_return_reason"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    reason_text: Mapped[str] = mapped_column(String(255), nullable=False)
    return_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
