# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class SsoProvider(str, enum.Enum):
    OKTA = "okta"
    AZURE_AD = "azure_ad"


class SsoConnection(Base):
    """One federated-login connection to an OIDC identity provider (Okta or Azure AD —
    both are fully OIDC-compliant, so the protocol handling in auth/oidc_service.py is
    generic; `provider` is for display/setup-help only). `customer_id` is None for a
    bank-wide connection (bank staff) or set to scope it to one customer's own IdP
    tenant — the same NULL-means-bank-wide shape as TenantMembership.customer_id,
    MlModel.customer_id, and ExceptionItem.customer_id.

    There is deliberately no flat "default security group" here — see
    SsoGroupMapping. `auto_provision` only governs whether a brand-new identity may be
    CREATED on first successful login; which security group a login resolves to (for a
    new or existing membership) is always driven by group-mapping, re-evaluated on
    every login (services/sso_service.py::complete_sso_login)."""

    __tablename__ = "sso_connection"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer.id"), nullable=True, index=True)

    provider: Mapped[SsoProvider] = mapped_column(
        Enum(SsoProvider, name="sso_provider", native_enum=False, length=20), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Fernet-encrypted at rest (auth/crypto.py) — never stored or returned in plaintext.
    client_secret_encrypted: Mapped[str] = mapped_column(String(1000), nullable=False)
    groups_claim_name: Mapped[str] = mapped_column(String(100), nullable=False, default="groups")

    auto_provision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SsoGroupMapping(Base):
    """Which of the federated org's own groups/roles (as delivered in the id_token claim
    named by SsoConnection.groups_claim_name) map to which OpenClear security group.
    Mandatory, not optional: a connection can't be activated with zero mapping rows
    (services/sso_service.py) — IdP authentication alone is never sufficient to grant
    access, current group membership is, re-checked on every login."""

    __tablename__ = "sso_group_mapping"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sso_connection.id"), nullable=False, index=True)

    external_group: Mapped[str] = mapped_column(String(255), nullable=False)
    security_group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("security_group.id"), nullable=False)
    # Lower wins if a login's claims match more than one mapping for the same connection.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
