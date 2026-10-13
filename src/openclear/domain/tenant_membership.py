# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class TenantMembership(Base):
    """What actually grants a User access to a Tenant, and which SecurityGroup governs
    that access — this is the join that makes cross-tenant identities possible: one User
    row can have a TenantMembership (each with its own security group) in more than one
    Tenant. Deactivating one membership (is_active=False) only removes access to that one
    tenant, not the identity itself or its other memberships.

    `customer_id` (nullable) is what makes a membership customer-scoped instead of
    tenant-wide: NULL means the classic bank-wide membership (unchanged from before
    customers existed); a real value means this membership's security_group only applies
    to that one Customer's data — used identically whether the person is bank staff
    assigned to specific clients or a customer's own employee. One user can now hold
    several memberships in the same tenant (one per customer, or one tenant-wide plus
    per-customer ones), which is why the unique constraint is on the triple rather than
    just (user_id, tenant_id) — see web/routers/tenant_switch.py, which now has to key its
    lookup on this row's own id rather than tenant_id alone.

    Note: standard SQL treats NULL as distinct from NULL for uniqueness purposes, so this
    constraint alone can't stop a user from ending up with two tenant-wide (customer_id
    IS NULL) memberships in the same tenant — that invariant is enforced in
    services/user_service.py before a membership is created, not by the database."""

    __tablename__ = "tenant_membership"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", "customer_id", name="uq_tenant_membership_user_tenant_customer"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)
    security_group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("security_group.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # When true, this membership may authenticate with a password even if the tenant or
    # customer otherwise requires SSO (auth/login_service.py::_scope_allows_password_login)
    # — but only alongside a mandatory WebAuthn key, re-verified on every login and on
    # every /ui/switch-tenant into this membership (never just opt-in like the ordinary
    # WebAuthn second factor). See services/user_service.py::grant_multi_customer_access,
    # the only place this is currently settable.
    require_webauthn: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Per-account access (services/account_access_service.py): a customer's person sees all
    # of the customer's accounts (True, the default), or only those in membership_account.
    # Only meaningful for a customer membership; bank staff always see the whole bank.
    all_accounts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MembershipAccount(Base):
    """An account a customer's person may see, when their membership isn't for all of the
    customer's accounts (TenantMembership.all_accounts)."""

    __tablename__ = "membership_account"
    __table_args__ = (UniqueConstraint("membership_id", "account_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant_membership.id"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id"), nullable=False)

