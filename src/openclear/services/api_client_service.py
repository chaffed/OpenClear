# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""API clients: OAuth 2.0 client credentials for a bank's own systems (SDK_PLAN.md P0;
domain/api_client.py).

A bank admin creates a client, picking a security group (what it may do) and optionally a
customer (whose data it may see). The client gets a service identity, a User that can't
sign in, with one membership carrying that group and customer, so the rest of the app
treats its requests like any member's: the same permission checks, customer scoping, RLS
binding and audit attribution.

The client exchanges its id and secret at `POST /api/v1/oauth/token` for a short-lived
access token (api_client_token_minutes). Revoking it deactivates the membership and the
service identity, which takes effect on its next request; rotating the secret keeps
tokens already issued until they expire, but no new one can be had with the old secret."""

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.auth.security import hash_password
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.api_client import ApiClient
from openclear.domain.customer import Customer
from openclear.domain.security_group import SecurityGroup
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User

_CLIENT_ID = re.compile(r"^occ_([0-9a-f]{32})_[A-Za-z0-9_-]{8,}$")
_SERVICE_EMAIL_DOMAIN = "api-client.invalid"  # .invalid is reserved: no inbox, no identity provider


class ApiClientError(ValueError):
    """Bad input when creating or changing a client; the message is fit to show."""


@dataclass(frozen=True, slots=True)
class IssuedSecret:
    client: ApiClient
    secret: str  # shown once; only its hash is kept


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _new_secret() -> str:
    return f"ocs_{secrets.token_urlsafe(32)}"


def tenant_from_client_id(client_id: str) -> uuid.UUID | None:
    match = _CLIENT_ID.match(client_id or "")
    return uuid.UUID(match.group(1)) if match else None


def list_clients(session: Session, tenant_id: uuid.UUID) -> list[ApiClient]:
    return list(session.execute(
        select(ApiClient).where(ApiClient.tenant_id == tenant_id).order_by(ApiClient.revoked_at.is_not(None), ApiClient.name)
    ).scalars())


def get_client(session: Session, tenant_id: uuid.UUID, api_client_id: uuid.UUID) -> ApiClient | None:
    client = session.get(ApiClient, api_client_id)
    return client if client is not None and client.tenant_id == tenant_id else None


def create_client(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    name: str,
    security_group_id: uuid.UUID,
    customer_id: uuid.UUID | None = None,
    can_act_for_users: bool = False,
    created_by_user_id: uuid.UUID | None = None,
    sso_connection_id: uuid.UUID | None = None,
    read_only: bool = False,
    active: bool = True,
) -> IssuedSecret:
    """`active` False: made, and its secret shown, but unusable until a second admin approves it
    (DUAL_CONTROL_PLAN.md; change kind api_client.activate)."""
    name = (name or "").strip()
    if sso_connection_id is not None:
        from openclear.domain.sso_connection import SsoConnection, SsoProtocol

        connection = session.get(SsoConnection, sso_connection_id)
        if connection is None or connection.tenant_id != tenant_id or connection.protocol != SsoProtocol.OIDC:
            raise ApiClientError("Choose one of this organization's OIDC sign-on connections.")
        if not can_act_for_users:
            raise ApiClientError("Linking a sign-on connection needs “may act for your users”.")
    if not name:
        raise ApiClientError("A name is required.")
    group = session.get(SecurityGroup, security_group_id)
    if group is None or group.tenant_id != tenant_id:
        raise ApiClientError("Security group not found.")
    if customer_id is not None:
        customer = session.get(Customer, customer_id)
        if customer is None or customer.tenant_id != tenant_id:
            raise ApiClientError("Customer not found.")

    client_id = f"occ_{tenant_id.hex}_{secrets.token_urlsafe(12)}"
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "client"
    service_user = User(
        email=f"{slug}.{secrets.token_hex(4)}@{_SERVICE_EMAIL_DOMAIN}",
        hashed_password=hash_password(secrets.token_urlsafe(48)),  # never used: login refuses service identities
        is_service_account=True,
        is_active=active,
    )
    session.add(service_user)
    session.flush()
    membership = TenantMembership(
        user_id=service_user.id, tenant_id=tenant_id, customer_id=customer_id, security_group_id=security_group_id, is_active=active,
    )
    session.add(membership)
    session.flush()
    secret = _new_secret()
    client = ApiClient(
        tenant_id=tenant_id, name=name, client_id=client_id, secret_hash=_hash(secret), user_id=service_user.id,
        membership_id=membership.id, can_act_for_users=can_act_for_users, created_by_user_id=created_by_user_id,
        sso_connection_id=sso_connection_id, read_only=read_only,
    )
    session.add(client)
    session.flush()
    return IssuedSecret(client, secret)


def rotate_secret(session: Session, tenant_id: uuid.UUID, api_client_id: uuid.UUID) -> IssuedSecret:
    client = get_client(session, tenant_id, api_client_id)
    if client is None or client.revoked_at is not None:
        raise ApiClientError("API client not found, or revoked.")
    secret = _new_secret()
    client.secret_hash = _hash(secret)
    client.secret_rotated_at = datetime.now(timezone.utc)
    session.flush()
    return IssuedSecret(client, secret)


def revoke(session: Session, tenant_id: uuid.UUID, api_client_id: uuid.UUID) -> ApiClient:
    """One-way. The service identity and its membership are deactivated, so even a token
    issued a minute ago stops working on its next request."""
    client = get_client(session, tenant_id, api_client_id)
    if client is None:
        raise ApiClientError("API client not found.")
    if client.revoked_at is None:
        client.revoked_at = datetime.now(timezone.utc)
        membership = session.get(TenantMembership, client.membership_id)
        if membership is not None:
            membership.is_active = False
        user = session.get(User, client.user_id)
        if user is not None:
            user.is_active = False
            user.token_version += 1
    session.flush()
    return client


def authenticate(session: Session, client_id: str, secret: str) -> ApiClient | None:
    """The client for these credentials, or None. Binds the session to the client's bank
    (RLS) on the way, since everything after reads that bank's rows."""
    tenant_id = tenant_from_client_id(client_id)
    if tenant_id is None or not secret:
        return None
    tenant = session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        return None
    bind_session_to_tenant(session, tenant_id)
    client = session.execute(select(ApiClient).where(ApiClient.client_id == client_id)).scalar_one_or_none()
    if client is None or client.revoked_at is not None or not hmac.compare_digest(client.secret_hash, _hash(secret)):
        return None
    if client.token_auth_method == "private_key_jwt":
        return None  # signs in with its key pair only (auth/client_assertion.py)
    membership = session.get(TenantMembership, client.membership_id)
    user = session.get(User, client.user_id)
    if membership is None or not membership.is_active or user is None or not user.is_active:
        return None
    client.last_used_at = datetime.now(timezone.utc)
    return client


def use_key_pair(session: Session, tenant_id: uuid.UUID, api_client_id: uuid.UUID, public_key_pem: str) -> ApiClient:
    """From now on the client signs in with an assertion signed by this key's private half
    (auth/client_assertion.py); its secret no longer works."""
    from openclear.auth.client_assertion import UnusableKey, load_public_key

    client = get_client(session, tenant_id, api_client_id)
    if client is None or client.revoked_at is not None:
        raise ApiClientError("API client not found.")
    try:
        load_public_key(public_key_pem)
    except UnusableKey as exc:
        raise ApiClientError(str(exc)) from None
    client.public_key_pem, client.token_auth_method = public_key_pem.strip(), "private_key_jwt"
    session.flush()
    return client


def use_secret(session: Session, tenant_id: uuid.UUID, api_client_id: uuid.UUID) -> IssuedSecret:
    """Back to a secret: a new one, shown once; the key pair no longer works."""
    client = get_client(session, tenant_id, api_client_id)
    if client is None or client.revoked_at is not None:
        raise ApiClientError("API client not found.")
    client.token_auth_method, client.public_key_pem = "client_secret", None
    session.flush()
    return rotate_secret(session, tenant_id, api_client_id)


# --- A customer's own clients (MOBILE_PLAN.md R4) ---------------------------------------------
# A self-managing customer makes clients for its own systems. Each is an ordinary client whose
# membership is the customer's, with a role the bank offers to customers and, optionally, some of
# the customer's accounts; never acting for users or linked to a sign-on connection (bank-only).
# The customer manages only the clients it made (managed_by "customer"); the bank sees and can
# revoke them on its own API clients page.


def list_customer_clients(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> list[ApiClient]:
    return list(session.execute(
        select(ApiClient).join(TenantMembership, TenantMembership.id == ApiClient.membership_id)
        .where(ApiClient.tenant_id == tenant_id, ApiClient.managed_by == "customer", TenantMembership.customer_id == customer_id)
        .order_by(ApiClient.revoked_at.is_not(None), ApiClient.name)
    ).scalars())


def get_customer_client(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, api_client_id: uuid.UUID) -> ApiClient:
    """One of the clients this customer made; anything else (another customer's, the bank's) is
    not found."""
    client = get_client(session, tenant_id, api_client_id)
    membership = session.get(TenantMembership, client.membership_id) if client is not None else None
    if client is None or client.managed_by != "customer" or membership is None or membership.customer_id != customer_id:
        raise ApiClientError("API client not found.")
    return client


def create_customer_client(
    session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, *, name: str, security_group_id: uuid.UUID, read_only: bool = False,
    all_accounts: bool = True, account_ids: list[uuid.UUID] | None = None, created_by_user_id: uuid.UUID | None = None,
) -> IssuedSecret:
    from openclear.services import account_access_service

    customer = session.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id or not customer.self_manages_api_clients:
        raise ApiClientError("Your bank doesn't let you manage your own API clients.")
    group = session.get(SecurityGroup, security_group_id)
    if group is None or group.tenant_id != tenant_id or not group.offered_to_customers:
        raise ApiClientError("Choose one of the roles your bank offers.")
    issued = create_client(session, tenant_id, name=name, security_group_id=security_group_id, customer_id=customer_id,
                           created_by_user_id=created_by_user_id, read_only=read_only)
    issued.client.managed_by = "customer"
    if not all_accounts:
        try:
            account_access_service.set_access(session, tenant_id, issued.client.membership_id, all_accounts=False, account_ids=account_ids)
        except account_access_service.AccountAccessError as exc:
            raise ApiClientError(str(exc)) from None
    session.flush()
    return issued


def describe(session: Session, client: ApiClient) -> dict:
    """What the API clients page and API show: no secret, ever."""
    from openclear.services import account_access_service

    membership = session.get(TenantMembership, client.membership_id)
    group = session.get(SecurityGroup, membership.security_group_id) if membership else None
    customer = session.get(Customer, membership.customer_id) if membership and membership.customer_id else None
    creator = session.get(User, client.created_by_user_id) if client.created_by_user_id else None
    accounts = None
    if customer is not None:
        access = account_access_service.get_access(session, client.tenant_id, membership.id)
        accounts = "All accounts" if access.all_accounts else account_access_service.describe(access).split(": ", 1)[-1]
    return {
        "id": client.id, "name": client.name, "client_id": client.client_id, "managed_by": client.managed_by, "accounts": accounts,
        "security_group": group.name if group else None, "customer": customer.name if customer else None,
        "can_act_for_users": client.can_act_for_users, "read_only": client.read_only, "token_auth_method": client.token_auth_method, "created_by": creator.email if creator else None,
        "sso_connection_id": client.sso_connection_id,
        "created_at": client.created_at, "secret_rotated_at": client.secret_rotated_at,
        "last_used_at": client.last_used_at, "revoked_at": client.revoked_at,
    }
