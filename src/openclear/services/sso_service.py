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
from openclear.domain.sso_connection import SsoAssertionSeen, SsoConnection, SsoGroupMapping, SsoIdentity, SsoProtocol, SsoProvider
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
    # OIDC: the issuer URL. SAML: the identity provider's entity ID.
    issuer: str
    client_id: str | None
    # None/empty on update means "keep the existing secret" — same convention as leaving
    # a password field blank on an edit form; required on create (OIDC).
    client_secret: str | None
    groups_claim_name: str
    auto_provision: bool
    customer_id: uuid.UUID | None = None
    protocol: SsoProtocol = SsoProtocol.OIDC
    saml_sso_url: str | None = None
    saml_slo_url: str | None = None
    saml_idp_certificate: str | None = None
    saml_email_attribute: str | None = None
    allow_idp_initiated: bool = False
    customer_claim: str | None = None


def _https_url(value: str | None, what: str, *, required: bool) -> str | None:
    """A URL the browser is sent to (never one the server calls), so it only has to be https."""
    value = (value or "").strip()
    if not value:
        if required:
            raise ValueError(f"The {what} is required.")
        return None
    if not value.startswith("https://") or len(value) > 1000:
        raise ValueError(f"The {what} must be an https:// URL.")
    return value


def _certificate(pem: str | None) -> str:
    """The identity provider's signing certificate, as PEM; accepts a bare base64 body too."""
    from cryptography import x509

    text = (pem or "").strip()
    if not text:
        raise ValueError("The identity provider's signing certificate is required.")
    if "-----BEGIN" not in text:
        body = "".join(text.split())
        text = "-----BEGIN CERTIFICATE-----\n" + "\n".join(body[i:i + 64] for i in range(0, len(body), 64)) + "\n-----END CERTIFICATE-----"
    try:
        x509.load_pem_x509_certificate(text.encode())
    except ValueError:
        raise ValueError("The identity provider's signing certificate isn't a valid X.509 certificate.") from None
    return text + "\n"


def _apply_input(session: Session, tenant_id: uuid.UUID, connection: SsoConnection, data: SsoConnectionInput) -> None:
    if data.customer_id is not None:
        if customer_service.get_customer(session, tenant_id, data.customer_id) is None:
            raise ValueError("Customer not found")
    connection.customer_id = data.customer_id
    connection.provider = data.provider
    connection.display_name = data.display_name
    connection.groups_claim_name = data.groups_claim_name
    connection.auto_provision = data.auto_provision
    connection.allow_idp_initiated = data.allow_idp_initiated
    customer_claim = (data.customer_claim or "").strip() or None
    if customer_claim and data.customer_id is not None:
        raise ValueError("The online banking profile (customer claim) is for a bank-wide connection, not one customer's.")
    if customer_claim and len(customer_claim) > 100:
        raise ValueError("The customer claim name is too long.")
    connection.customer_claim = customer_claim
    if connection.protocol is not None and connection.protocol != data.protocol and connection.id is not None and connection.tenant_id:
        raise ValueError("A connection's protocol can't be changed; add a new connection instead.")
    connection.protocol = data.protocol
    if data.protocol == SsoProtocol.SAML:
        entity_id = (data.issuer or "").strip()
        if not entity_id or len(entity_id) > 500:
            raise ValueError("The identity provider's entity ID is required.")
        connection.issuer = entity_id
        connection.saml_sso_url = _https_url(data.saml_sso_url, "sign-in URL", required=True)
        connection.saml_slo_url = _https_url(data.saml_slo_url, "sign-out URL", required=False)
        connection.saml_idp_certificate = _certificate(data.saml_idp_certificate)
        connection.saml_email_attribute = (data.saml_email_attribute or "").strip() or None
        connection.client_id, connection.client_secret_encrypted = None, None
        return
    # Rejects http://, localhost, and private-IP issuers up front with a clear form
    # error (UnsafeUrlError is a ValueError) — auth/outbound_http.py re-checks, including
    # DNS, every time the server actually connects.
    connection.issuer = check_url(data.issuer.strip().rstrip("/"), what="issuer URL")
    if not (data.client_id or "").strip():
        raise ValueError("Client ID is required")
    connection.client_id = data.client_id.strip()
    if data.client_secret:
        connection.client_secret_encrypted = encrypt_secret(data.client_secret)


def create_connection(session: Session, tenant_id: uuid.UUID, data: SsoConnectionInput) -> SsoConnection:
    if data.protocol == SsoProtocol.OIDC and not data.client_secret:
        raise ValueError("Client secret is required")
    connection = SsoConnection(id=uuid.uuid4(), tenant_id=tenant_id)
    connection.protocol = None  # set by _apply_input
    _apply_input(session, tenant_id, connection, data)
    if connection.protocol == SsoProtocol.SAML:
        from openclear.auth.saml_service import generate_sp_keypair

        connection.saml_sp_certificate, connection.saml_sp_private_key_encrypted = generate_sp_keypair(connection.id)
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
    result = _complete_sso_login(session, tenant_id, connection, claims)
    if result.outcome == "success" and claims.subject:
        record_identity(session, tenant_id, connection, claims.subject, result.user)
    return result


def record_identity(session: Session, tenant_id: uuid.UUID, connection: SsoConnection, subject: str, user: User) -> SsoIdentity:
    """Remembers who this person is at the provider (its `sub` or NameID), so the provider
    can refer to them later (back-channel logout, SCIM) by an id that doesn't change."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    identity = session.execute(
        select(SsoIdentity).where(SsoIdentity.connection_id == connection.id, SsoIdentity.subject == subject)
    ).scalar_one_or_none()
    if identity is None:
        identity = SsoIdentity(tenant_id=tenant_id, connection_id=connection.id, subject=subject, user_id=user.id)
        session.add(identity)
    identity.user_id = user.id
    identity.last_login_at = datetime.now(timezone.utc)
    session.flush()
    return identity


def find_identity_user(session: Session, connection: SsoConnection, subject: str) -> User | None:
    from sqlalchemy import select

    identity = session.execute(
        select(SsoIdentity).where(SsoIdentity.connection_id == connection.id, SsoIdentity.subject == subject)
    ).scalar_one_or_none()
    return session.get(User, identity.user_id) if identity else None


class Replayed(ValueError):
    """An assertion or logout token that was already used."""


def record_assertion(session: Session, tenant_id: uuid.UUID, connection: SsoConnection, assertion_id: str, expires_at) -> None:
    """Accepts each assertion (or logout token) once, until it would expire anyway."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    if not assertion_id:
        raise Replayed("The assertion has no ID.")
    seen = session.execute(
        select(SsoAssertionSeen.id).where(SsoAssertionSeen.connection_id == connection.id, SsoAssertionSeen.assertion_id == assertion_id)
    ).first()
    if seen:
        raise Replayed("This sign-in was already used.")
    try:
        with session.begin_nested():
            session.add(SsoAssertionSeen(
                tenant_id=tenant_id, connection_id=connection.id, assertion_id=assertion_id[:255],
                expires_at=expires_at or datetime.now(timezone.utc) + timedelta(minutes=10),
            ))
    except IntegrityError:
        raise Replayed("This sign-in was already used.") from None


def _complete_sso_login(session: Session, tenant_id: uuid.UUID, connection: SsoConnection, claims: OidcClaims) -> SsoLoginResult:
    """Implements the core of "the federated org's own groups always gate access": every
    call re-resolves the security group from the CURRENT claims, regardless of whether a
    membership already exists — see domain/sso_connection.py's docstrings for the full
    rationale. Never a generic exception; every outcome is a plain, expected case a
    caller renders a specific message for."""
    scope_customer_id = connection.customer_id
    if connection.customer_claim:
        # The online banking sign-on profile: the provider says which business customer the
        # person belongs to; a missing or unknown customer gets no access at all.
        customer = customer_service.get_customer_by_number(session, tenant_id, claims.customer_number) if claims.customer_number else None
        if customer is None or not getattr(customer, "is_active", True):
            return SsoLoginResult(outcome="not_authorized")
        scope_customer_id = customer.id
    user = UserRepository(session).get_by_email(claims.email)
    existing_membership: TenantMembership | None = None
    if user is not None:
        candidates = [
            m for m in TenantMembershipRepository(session, tenant_id).list(user_id=user.id) if m.customer_id == scope_customer_id
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

    from openclear.services import customer_sign_on_service

    if not customer_sign_on_service.may_create_person(session, connection, claims.email):
        # A customer's own identity provider can assert any email: it creates people only in
        # the customer's verified domains (CUSTOMER_ADMIN_PLAN.md, guardrail 5).
        return SsoLoginResult(outcome="not_provisioned")

    if user is not None:
        # An identity provider can assert any email. Linking it to an existing person who
        # isn't already a member here (in this connection's scope) would let one bank's, or one
        # customer's, identity provider take over someone else's identity: refuse, and leave it
        # to an admin to add them (which asks the person's other organizations' consent).
        return SsoLoginResult(outcome="not_provisioned")

    user = user_service.create_user_with_membership(
        session,
        tenant_id,
        email=claims.email,
        password=_generate_unusable_password(),
        security_group_id=resolved_group_id,
        customer_id=scope_customer_id,
    )
    new_membership = [
        m for m in TenantMembershipRepository(session, tenant_id).list(user_id=user.id) if m.customer_id == scope_customer_id
    ][0]
    return SsoLoginResult(outcome="success", user=user, membership=new_membership)


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
