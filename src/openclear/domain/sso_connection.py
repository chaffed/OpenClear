# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class SsoProvider(str, enum.Enum):
    """Which identity provider, for labels and setup help only: the protocol handling is
    generic (auth/oidc_service.py, auth/saml_service.py)."""

    OKTA = "okta"
    AZURE_AD = "azure_ad"
    PING = "ping"
    GOOGLE = "google"
    KEYCLOAK = "keycloak"
    OTHER = "other"


class SsoProtocol(str, enum.Enum):
    OIDC = "oidc"
    SAML = "saml"


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
    protocol: Mapped[SsoProtocol] = mapped_column(
        Enum(SsoProtocol, name="sso_protocol", native_enum=False, length=10), nullable=False, default=SsoProtocol.OIDC,
        server_default="OIDC",
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # OIDC: the issuer URL. SAML: the identity provider's entity ID.
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    # OIDC only.
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Fernet-encrypted at rest (auth/crypto.py) — never stored or returned in plaintext.
    client_secret_encrypted: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # The claim (OIDC) or attribute (SAML) carrying the person's groups.
    groups_claim_name: Mapped[str] = mapped_column(String(100), nullable=False, default="groups")

    # SAML only (auth/saml_service.py). The identity provider's sign-in and sign-out URLs
    # and signing certificate (PEM); which attribute carries the email (blank: the NameID).
    # OpenClear's own key pair for this connection is generated when it's created, so the
    # provider can encrypt assertions to it; the private key is stored encrypted.
    saml_sso_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    saml_slo_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    saml_idp_certificate: Mapped[str | None] = mapped_column(Text, nullable=True)
    saml_email_attribute: Mapped[str | None] = mapped_column(String(200), nullable=True)
    saml_sp_certificate: Mapped[str | None] = mapped_column(Text, nullable=True)
    saml_sp_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Sign-in started at the identity provider (a tile in Okta or Azure): off unless the
    # bank turns it on, since there's no request of ours to match the answer to.
    allow_idp_initiated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

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


class SsoIdentity(Base):
    """Who a person is at an identity provider: the provider's own stable id for them (OIDC
    `sub`, SAML NameID) for one connection, linked to their OpenClear user. Recorded at each
    sign-in; it's how back-channel logout, SCIM and the online banking sign-on profile find
    the person (an email can change at the provider, its own id doesn't)."""

    __tablename__ = "sso_identity"
    __table_args__ = (UniqueConstraint("connection_id", "subject", name="uq_sso_identity_subject"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sso_connection.id"), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SsoAssertionSeen(Base):
    """A SAML assertion (or OIDC back-channel logout token) already used, kept until it
    expires, so a captured one can't be replayed. Housekeeping removes expired rows."""

    __tablename__ = "sso_assertion_seen"
    __table_args__ = (UniqueConstraint("connection_id", "assertion_id", name="uq_sso_assertion_seen"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sso_connection.id"), nullable=False)
    assertion_id: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
