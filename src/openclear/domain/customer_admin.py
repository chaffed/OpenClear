# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Customers managing their own people and sign-on (CUSTOMER_ADMIN_PLAN.md)."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class UserInvitation(Base):
    """A customer administrator's invitation to one person (services/customer_people_service.py).
    Nothing is created for the person until they accept: a new person then chooses a password,
    someone who already exists confirms with theirs. The link carries a secret; only its
    SHA-256 is kept. One use, and it expires."""

    __tablename__ = "user_invitation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    security_group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("security_group.id"), nullable=False)
    all_accounts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    account_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    require_webauthn: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CustomerEmailDomain(Base):
    """An email domain a customer, or the bank itself (customer_id null, since 1.24), says is its
    own, and whether it has proved it with a DNS TXT record (services/customer_sign_on_service.py).
    A customer-managed sign-on connection creates people automatically only for verified domains,
    and SSO moves a person to a new email only within one (OWASP_PLAN.md OW-D31, OW-D32)."""

    __tablename__ = "customer_email_domain"
    __table_args__ = (UniqueConstraint("tenant_id", "domain"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    verification_token: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
