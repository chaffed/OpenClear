# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class User(Base):
    """A login identity — global, not tenant-owned. What a user can access in a given
    tenant comes from their TenantMembership row(s) there, not from anything on this
    table, which is what lets one identity (one password, one set of registered WebAuthn
    keys) hold membership in more than one tenant. `email` is globally unique for exactly
    that reason: two different tenants must resolve the same email to the same identity,
    not two separate accounts that happen to share an address."""

    __tablename__ = "user"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Account lockout (auth/login_service.py::authenticate_password) — global to the
    # identity, not per-tenant, since a brute force targets this one password regardless
    # of which tenant the attempt is against. Reset to 0/None on the next correct
    # password; an admin can also clear locked_until early (services/user_service.py::unlock_user).
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set when an admin resets this user's password to a one-time temporary value
    # (services/user_service.py::admin_reset_password); cleared once the user sets their
    # own (change_own_password). While set, auth/deps.py surfaces it on TenantContext and
    # every web route except the change-password page redirects there (web/deps.py), and
    # the JSON API refuses the session outright.
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Stamped into every token as the `tv` claim (auth/security.py). Incrementing it
    # (services/session_service.py::revoke_all_sessions — on a password change or reset,
    # or an admin's "Sign out everywhere") invalidates every token this user holds on
    # every device, since auth/deps.py rejects a token whose tv doesn't match.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Set by the user themselves on /ui/security/notifications — nullable since SMS
    # notifications are opt-in and most users will never set one. No format validation
    # beyond what services/notification_service.py's SMS provider itself rejects; kept
    # as a plain string (not a typed phone type) same as Customer.phone.
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
