# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import secrets
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from openclear.auth.crypto import encrypt_secret
from openclear.auth.oidc_service import OidcClaims
from openclear.domain.customer import Customer
from openclear.domain.sso_connection import SsoConnection, SsoGroupMapping, SsoProvider
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.repositories.sso_connection_repo import SsoConnectionRepository
from openclear.repositories.sso_group_mapping_repo import SsoGroupMappingRepository
from openclear.auth.outbound_http import check_url
from openclear.repositories.tenant_membership_repo import TenantMembershipRepository
from openclear.repositories.user_repo import UserRepository
from openclear.services import customer_service, security_group_service, user_service


@dataclass(frozen=True, slots=True)
class SsoConnectionInput:
    provider: SsoProvider
    display_name: str
    issuer: str
    client_id: str
    # None/empty on update means "keep the existing secret" — same convention as leaving
    # a password field blank on an edit form; required on create.
    client_secret: str | None
    groups_claim_name: str
    auto_provision: bool
    customer_id: uuid.UUID | None = None


def _apply_input(session: Session, tenant_id: uuid.UUID, connection: SsoConnection, data: SsoConnectionInput) -> None:
    if data.customer_id is not None:
        if customer_service.get_customer(session, tenant_id, data.customer_id) is None:
            raise ValueError("Customer not found")
    connection.customer_id = data.customer_id
    connection.provider = data.provider
    connection.display_name = data.display_name
    # Rejects http://, localhost, and private-IP issuers up front with a clear form
    # error (UnsafeUrlError is a ValueError) — auth/outbound_http.py re-checks, including
    # DNS, every time the server actually connects.
    connection.issuer = check_url(data.issuer.strip().rstrip("/"), what="issuer URL")
    connection.client_id = data.client_id
    connection.groups_claim_name = data.groups_claim_name
    connection.auto_provision = data.auto_provision
    if data.client_secret:
        connection.client_secret_encrypted = encrypt_secret(data.client_secret)


def create_connection(session: Session, tenant_id: uuid.UUID, data: SsoConnectionInput) -> SsoConnection:
    if not data.client_secret:
        raise ValueError("Client secret is required")
    # client_secret_encrypted is always overwritten by _apply_input below (data.client_secret
    # is guaranteed truthy here) before this row is ever flushed to the database.
    connection = SsoConnection(client_secret_encrypted="")
    _apply_input(session, tenant_id, connection, data)
    SsoConnectionRepository(session, tenant_id).add(connection)
    session.flush()
    return connection


def update_connection(session: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID, data: SsoConnectionInput) -> SsoConnection:
    connection = SsoConnectionRepository(session, tenant_id).get(connection_id)
    if connection is None:
        raise ValueError("Connection not found")
    _apply_input(session, tenant_id, connection, data)
    session.flush()
    return connection


def list_connections(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None = None) -> list[SsoConnection]:
    repo = SsoConnectionRepository(session, tenant_id)
    stmt = repo.query().where(SsoConnection.customer_id == customer_id).order_by(SsoConnection.created_at)
    return list(session.execute(stmt).scalars().all())


def get_connection(session: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID) -> SsoConnection | None:
    return SsoConnectionRepository(session, tenant_id).get(connection_id)


def deactivate_connection(session: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID) -> SsoConnection | None:
    connection = SsoConnectionRepository(session, tenant_id).get(connection_id)
    if connection is None:
        return None
    connection.is_active = False
    session.flush()
    return connection


def reactivate_connection(session: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID) -> SsoConnection | None:
    connection = SsoConnectionRepository(session, tenant_id).get(connection_id)
    if connection is None:
        return None
    connection.is_active = True
    session.flush()
    return connection


def get_login_connections(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None = None) -> list[SsoConnection]:
    """Active connections for one scope that ALSO have at least one group mapping
    configured — what the login page renders "Sign in with X" buttons from. A connection
    with zero mappings would refuse every single login (see complete_sso_login), so it's
    not offered as a usable option until an admin has actually finished configuring it."""
    repo = SsoConnectionRepository(session, tenant_id)
    stmt = repo.query().where(SsoConnection.customer_id == customer_id, SsoConnection.is_active.is_(True))
    connections = list(session.execute(stmt).scalars().all())
    mapping_repo = SsoGroupMappingRepository(session, tenant_id)
    return [c for c in connections if mapping_repo.list(connection_id=c.id)]


# --- Group mappings ---


def list_group_mappings(session: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID) -> list[SsoGroupMapping]:
    return sorted(SsoGroupMappingRepository(session, tenant_id).list(connection_id=connection_id), key=lambda m: m.priority)


def add_group_mapping(
    session: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID, *, external_group: str, security_group_id: uuid.UUID,
    priority: int = 100,
) -> SsoGroupMapping:
    if SsoConnectionRepository(session, tenant_id).get(connection_id) is None:
        raise ValueError("Connection not found")
    if security_group_service.get_security_group(session, tenant_id, security_group_id) is None:
        raise ValueError("Security group not found")
    mapping = SsoGroupMapping(
        connection_id=connection_id, external_group=external_group, security_group_id=security_group_id, priority=priority
    )
    SsoGroupMappingRepository(session, tenant_id).add(mapping)
    session.flush()
    return mapping


def remove_group_mapping(session: Session, tenant_id: uuid.UUID, mapping_id: uuid.UUID) -> bool:
    mapping = SsoGroupMappingRepository(session, tenant_id).get(mapping_id)
    if mapping is None:
        return False
    session.delete(mapping)
    session.flush()
    return True


# --- Login-time resolution ---


@dataclass(frozen=True, slots=True)
class SsoLoginResult:
    outcome: Literal["success", "not_authorized", "not_provisioned"]
    user: User | None = None
    membership: TenantMembership | None = None


def _generate_unusable_password() -> str:
    # Auto-provisioned identities never set a password — this is stored (hashed) only to
    # satisfy User.hashed_password's NOT NULL constraint; it's never revealed and never
    # meant to be usable for password login.
    return secrets.token_urlsafe(48)


def _resolve_security_group_id(
    session: Session, tenant_id: uuid.UUID, connection_id: uuid.UUID, groups: list[str]
) -> uuid.UUID | None:
    mappings = SsoGroupMappingRepository(session, tenant_id).list(connection_id=connection_id)
    matches = [m for m in mappings if m.external_group in groups]
    if not matches:
        return None
    return min(matches, key=lambda m: m.priority).security_group_id


def complete_sso_login(session: Session, tenant_id: uuid.UUID, connection: SsoConnection, claims: OidcClaims) -> SsoLoginResult:
    """Implements the core of "the federated org's own groups always gate access": every
    call re-resolves the security group from the CURRENT claims, regardless of whether a
    membership already exists — see domain/sso_connection.py's docstrings for the full
    rationale. Never a generic exception; every outcome is a plain, expected case a
    caller renders a specific message for."""
    user = UserRepository(session).get_by_email(claims.email)
    existing_membership: TenantMembership | None = None
    if user is not None:
        candidates = [
            m for m in TenantMembershipRepository(session, tenant_id).list(user_id=user.id) if m.customer_id == connection.customer_id
        ]
        existing_membership = candidates[0] if candidates else None

    if user is not None and (not user.is_active or user.is_service_account):
        # An API client's service identity never signs in, whatever an identity
        # provider claims about the email.
        return SsoLoginResult(outcome="not_authorized")

    resolved_group_id = _resolve_security_group_id(session, tenant_id, connection.id, claims.groups)

    if resolved_group_id is None:
        # No matching group today — revoke any access this route previously granted
        # rather than leaving a stale, now-unjustified membership quietly active.
        if existing_membership is not None and existing_membership.is_active:
            existing_membership.is_active = False
            session.flush()
        return SsoLoginResult(outcome="not_authorized")

    if existing_membership is not None:
        if existing_membership.security_group_id != resolved_group_id:
            existing_membership.security_group_id = resolved_group_id
        if not existing_membership.is_active:
            existing_membership.is_active = True
        session.flush()
        return SsoLoginResult(outcome="success", user=user, membership=existing_membership)

    if not connection.auto_provision:
        return SsoLoginResult(outcome="not_provisioned")

    if user is None:
        user = user_service.create_user_with_membership(
            session,
            tenant_id,
            email=claims.email,
            password=_generate_unusable_password(),
            security_group_id=resolved_group_id,
            customer_id=connection.customer_id,
        )
        new_membership = [
            m for m in TenantMembershipRepository(session, tenant_id).list(user_id=user.id) if m.customer_id == connection.customer_id
        ][0]
        return SsoLoginResult(outcome="success", user=user, membership=new_membership)

    membership = TenantMembership(
        user_id=user.id, tenant_id=tenant_id, customer_id=connection.customer_id, security_group_id=resolved_group_id
    )
    TenantMembershipRepository(session, tenant_id).add(membership)
    session.flush()
    return SsoLoginResult(outcome="success", user=user, membership=membership)


# --- Password-login enforcement mode (per scope, guarded against lockout) ---


def set_tenant_password_login_enabled(session: Session, tenant_id: uuid.UUID, enabled: bool) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise ValueError("Tenant not found")
    if not enabled and not get_login_connections(session, tenant_id, customer_id=None):
        raise ValueError(
            "Cannot require SSO: no active SSO connection with a group mapping exists for this organization yet"
        )
    tenant.password_login_enabled = enabled
    session.flush()
    return tenant


def set_customer_password_login_enabled(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, enabled: bool) -> Customer:
    customer = customer_service.get_customer(session, tenant_id, customer_id)
    if customer is None:
        raise ValueError("Customer not found")
    if not enabled and not get_login_connections(session, tenant_id, customer_id=customer_id):
        raise ValueError("Cannot require SSO: no active SSO connection with a group mapping exists for this customer yet")
    customer.password_login_enabled = enabled
    session.flush()
    return customer
