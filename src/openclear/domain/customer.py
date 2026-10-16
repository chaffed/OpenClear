# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class Customer(Base):
    """A bank tenant's own business client — the sub-organization each Account, and every
    record that hangs off one (issued_item, stop_payment, paid_item,
    ach_authorization_rule, ach_transaction — see their customer_id columns), can be
    assigned to. Segregating access by customer is layered entirely on top of the
    existing tenant model: TenantMembership.customer_id (see that module) is what
    restricts a login to just this customer's data, whether the person is the bank's own
    staff assigned to this client or the client's own employee. No hard delete —
    deactivate via is_active, consistent with this app's philosophy elsewhere."""

    __tablename__ = "customer"
    __table_args__ = (UniqueConstraint("tenant_id", "customer_number", name="uq_customer_tenant_number"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_number: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Independent of Tenant.password_login_enabled — a customer's own SSO enforcement is
    # configured separately from the bank's, same as its own SsoConnection rows are. Only
    # makes sense set False once this customer has at least one active SsoConnection
    # (enforced in services/sso_service.py).
    password_login_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Self-service (CUSTOMER_ADMIN_PLAN.md): the bank lets the customer's own administrators
    # manage its people (customer_user:manage) and its sign-on (customer_sso:manage). Off
    # until the bank turns them on; turning one off suspends it at once.
    self_manages_users: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    self_manages_sso: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Its own API clients (MOBILE_PLAN.md R4; customer_api_client:manage). Turning it off stops
    # the customer managing them; the clients it made keep working until revoked.
    self_manages_api_clients: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Whether new people its side adds wait for the bank's approval (MOBILE_PLAN.md R6): None
    # follows the bank (Tenant.approve_customer_users), True or False overrides it.
    approve_users: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # This customer's own ADDITIONAL password requirements, on top of the tenant's own
    # policy (domain/tenant.py) — see auth/password_policy.py::effective_policy. None
    # means "no additional minimum, just use the tenant's"; the 4 boolean flags default
    # False for the same reason (False and "not set" are behaviorally identical once
    # combined with the tenant's own flag via OR) so there's no need for nullable bools.
    password_min_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    password_require_uppercase: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_require_lowercase: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_require_number: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_require_symbol: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # When the row last changed: what list endpoints' updated_since filters on, so a bank's
    # data pipeline fetches only what changed (docs/guides/bi.md).
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Cross-reference to an identifier from an outside system (e.g. a core-banking CIF
    # number) — distinct from customer_number above, which is this app's own tenant-scoped
    # identifier that bulk files already match against.
    external_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    primary_contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)

    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Markdown source (validated/normalized at save time by services/message_content.py,
    # rendered+sanitized at display time by web/templates.py::render_markdown and
    # domain/tenant.py's identical banner_message column), shown in the same persistent
    # top-of-page banner as the tenant's own banner_message, but only to this customer's
    # own scoped users. Genuinely self-service: unlike this table's password_* columns
    # above (configured by bank staff holding customer:manage, which is masked out of
    # every customer-scoped session), this is gated by customer_banner:manage, which is
    # deliberately NOT masked — see auth/permissions.py — so the customer's own login can
    # set this directly. Text, not a capped String -- see Tenant.banner_message's own
    # comment for why.
    banner_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # This customer's own reminder timing (services/reminder_service.py), set by its own
    # users holding customer_banner:manage: null follows the bank's, "" turns them off.
    reminder_offsets_minutes: Mapped[str | None] = mapped_column(String(64), nullable=True)
