# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class ApiClient(Base):
    """A bank's system (its core, its online banking provider, its middleware) calling the
    API with OAuth 2.0 client credentials instead of a person's password (SDK_PLAN.md P0;
    services/api_client_service.py).

    Each client acts through its own **service identity**: a User that can't sign in
    (`is_service_account`) with one membership in this bank. The membership carries the
    security group the bank chose (what the client may do) and, optionally, a customer
    (whose data it may see). So every existing permission check, customer scope, RLS
    binding and audit attribution applies to it unchanged, and revoking it (deactivating
    the membership) takes effect on the client's next request.

    `client_id` is public and embeds the bank's id (`occ_<tenant hex>_<random>`), so the
    token endpoint can bind to the right bank before reading this RLS-protected table.
    Only a SHA-256 of the secret is kept: it's 256 bits of randomness, shown once."""

    __tablename__ = "api_client"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    client_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant_membership.id"), nullable=False)
    # Whether it may act for one of the bank's (or its customer's) users, as an online
    # banking provider's server does for a signed-in customer (SDK_PLAN.md P0). Off unless
    # the bank turns it on.
    can_act_for_users: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Read-only credentials (docs/guides/bi.md): whatever the security group allows, this
    # client only ever has its *:read permissions (auth/deps.py). For a data pipeline, and for
    # handing a customer a feed of its own data (with customer_id set on its membership).
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # How the client signs in at the token endpoint: "client_secret" (the default), or
    # "private_key_jwt" (RFC 7523): an assertion signed with the private key whose public half
    # is here, and no secret accepted (auth/client_assertion.py).
    token_auth_method: Mapped[str] = mapped_column(String(20), nullable=False, default="client_secret", server_default="client_secret")
    public_key_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The online banking provider's SSO connection whose ID tokens this client may exchange
    # for a token acting for that person (RFC 8693; api/v1/oauth.py), and whose subject ids
    # OpenClear-Act-As-Subject refers to. Needs can_act_for_users.
    sso_connection_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sso_connection.id"), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    secret_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClientAssertionUse(Base):
    """A client assertion's `jti`, kept until the assertion expires, so it can't be used twice
    (auth/client_assertion.py). Housekeeping clears expired ones."""

    __tablename__ = "client_assertion_use"
    __table_args__ = (UniqueConstraint("api_client_id", "jti"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id"), nullable=False, index=True)
    api_client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_client.id"), nullable=False)
    jti: Mapped[str] = mapped_column(String(200), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

