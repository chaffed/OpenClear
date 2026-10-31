# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A customer's own administrators setting up its sign-on (CUSTOMER_ADMIN_PLAN.md, CA3):
connections to its identity provider, its email domains, SCIM and whether its people may
still sign in with a password. The connections themselves are services/sso_service.py's;
this adds what changes when a customer, not the bank, controls them:

- they're the customer's alone (customer_id, managed_by "customer"), and map only to the
  roles the bank offers;
- an identity provider can assert any email, so a customer-managed connection (by sign-in
  or SCIM) creates people only in the customer's verified email domains (a DNS TXT record);
- password sign-in can be turned off only from a session that signed in through sign-on,
  so the administrator turning it off has just shown it works; the bank can always turn it
  back on."""

import re
import secrets
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.errors import UserFacingError
from openclear.domain.customer import Customer
from openclear.domain.customer_admin import CustomerEmailDomain
from openclear.domain.security_group import SecurityGroup
from openclear.domain.sso_connection import SsoConnection, SsoGroupMapping
from openclear.services import sso_service

MANAGED_BY_CUSTOMER = "customer"
TXT_PREFIX = "openclear-verification="
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
# Mailbox providers anyone can sign up to: owning an address there isn't owning the domain.
_SHARED_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "ymail.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.net", "mail.com",
    "zoho.com", "yandex.com", "fastmail.com", "hey.com",
})


class SignOnError(UserFacingError):
    """Refused; the message is fit to show."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def customer(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
    found = session.get(Customer, customer_id)
    if found is None or found.tenant_id != tenant_id or not found.is_active:
        raise SignOnError("Customer not found.")
    if not found.self_manages_sso:
        raise SignOnError("Your bank manages this organization's sign-on.")
    return found


def connection(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, connection_id: uuid.UUID, *,
               editable: bool = True) -> SsoConnection:
    """One of the customer's connections; `editable` also requires the customer set it up
    (one the bank set up for it is shown, not changed)."""
    customer(session, tenant_id, customer_id)
    found = sso_service.get_connection(session, tenant_id, connection_id)
    if found is None or found.customer_id != customer_id:
        raise SignOnError("Connection not found.")
    if editable and found.managed_by != MANAGED_BY_CUSTOMER:
        raise SignOnError("Your bank set up this connection, so only it can change it.")
    return found


def create(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, data: sso_service.SsoConnectionInput) -> SsoConnection:
    customer(session, tenant_id, customer_id)
    data = replace(data, customer_id=customer_id, customer_claim=None)  # the customer's own, for its own people
    try:
        created = sso_service.create_connection(session, tenant_id, data)
    except ValueError as exc:
        raise SignOnError(str(exc)) from None
    created.managed_by = MANAGED_BY_CUSTOMER
    session.flush()
    return created


def update(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, connection_id: uuid.UUID,
           data: sso_service.SsoConnectionInput) -> SsoConnection:
    connection(session, tenant_id, customer_id, connection_id)
    data = replace(data, customer_id=customer_id, customer_claim=None)
    try:
        return sso_service.update_connection(session, tenant_id, connection_id, data)
    except ValueError as exc:
        raise SignOnError(str(exc)) from None


def set_active(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, connection_id: uuid.UUID, active: bool) -> SsoConnection:
    found = connection(session, tenant_id, customer_id, connection_id)
    if not active:
        _keep_a_way_in(session, tenant_id, customer_id, without=found.id)
    (sso_service.reactivate_connection if active else sso_service.deactivate_connection)(session, tenant_id, connection_id)
    return found


def add_mapping(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, connection_id: uuid.UUID, *, external_group: str,
                security_group_id: uuid.UUID, priority: int = 100) -> SsoGroupMapping:
    connection(session, tenant_id, customer_id, connection_id)
    group = session.get(SecurityGroup, security_group_id)
    if group is None or group.tenant_id != tenant_id or not group.offered_to_customers:
        raise SignOnError("Choose one of the roles your bank offers.")
    if not (external_group or "").strip():
        raise SignOnError("Enter the group's name as your identity provider sends it.")
    try:
        return sso_service.add_group_mapping(session, tenant_id, connection_id, external_group=external_group.strip(),
                                             security_group_id=security_group_id, priority=priority)
    except ValueError as exc:
        raise SignOnError(str(exc)) from None


def remove_mapping(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, connection_id: uuid.UUID, mapping_id: uuid.UUID) -> None:
    connection(session, tenant_id, customer_id, connection_id)
    mapping = session.get(SsoGroupMapping, mapping_id)
    if mapping is None or mapping.connection_id != connection_id:
        raise SignOnError("Mapping not found.")
    others = [m for m in sso_service.list_group_mappings(session, tenant_id, connection_id) if m.id != mapping_id]
    if not others:
        _keep_a_way_in(session, tenant_id, customer_id, without=connection_id)
    sso_service.remove_group_mapping(session, tenant_id, mapping_id)


def _keep_a_way_in(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, *, without: uuid.UUID) -> None:
    """With password sign-in off, the customer's people need some connection that works."""
    found = session.get(Customer, customer_id)
    if found.password_login_enabled:
        return
    remaining = [c for c in sso_service.get_login_connections(session, tenant_id, customer_id=customer_id) if c.id != without]
    if not remaining:
        raise SignOnError("Password sign-in is off, so this is your people's only way in. Turn password sign-in back on first.")


def set_password_sign_in(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, *, enabled: bool,
                         signed_in_through_sign_on: bool) -> Customer:
    """Turning it off needs a working connection, and the administrator doing it must have
    signed in through one this session, so nobody turns it off without seeing sign-on work."""
    customer(session, tenant_id, customer_id)
    if not enabled and not signed_in_through_sign_on:
        raise SignOnError("Sign in through your sign-on first (use “Test sign-in”), then turn off passwords from that session.")
    try:
        return sso_service.set_customer_password_login_enabled(session, tenant_id, customer_id, enabled=enabled)
    except ValueError as exc:
        raise SignOnError(str(exc)) from None


# --- Email domains --------------------------------------------------------------------------


def _owned_by(customer_id: uuid.UUID | None):
    """A customer's domains, or with no customer the bank's own (OWASP_PLAN.md OW-D32)."""
    return CustomerEmailDomain.customer_id.is_(None) if customer_id is None else CustomerEmailDomain.customer_id == customer_id


def domains(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> list[CustomerEmailDomain]:
    return list(session.execute(select(CustomerEmailDomain).where(
        CustomerEmailDomain.tenant_id == tenant_id, _owned_by(customer_id)).order_by(CustomerEmailDomain.domain)).scalars())


def verified_domain(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, email: str) -> bool:
    """Whether the email's domain is verified for this customer, or for the bank with no customer."""
    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    return session.execute(select(CustomerEmailDomain.id).where(
        CustomerEmailDomain.tenant_id == tenant_id, _owned_by(customer_id), CustomerEmailDomain.domain == domain,
        CustomerEmailDomain.verified_at.is_not(None))).first() is not None


def add_domain(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, domain: str) -> CustomerEmailDomain:
    if customer_id is not None:
        customer(session, tenant_id, customer_id)
    domain = (domain or "").strip().lower().rstrip(".").removeprefix("@")
    if not _DOMAIN.match(domain):
        raise SignOnError("Enter a domain such as acme.com.")
    if domain in _SHARED_MAIL_DOMAINS:
        raise SignOnError("Anyone can have an address there, so it can't be verified as yours.")
    taken = session.execute(select(CustomerEmailDomain).where(
        CustomerEmailDomain.tenant_id == tenant_id, CustomerEmailDomain.domain == domain)).scalar_one_or_none()
    if taken is not None:
        raise SignOnError("That domain is already added" + (" here." if taken.customer_id == customer_id else
                                                            " for the bank." if taken.customer_id is None else " for another customer."))
    row = CustomerEmailDomain(tenant_id=tenant_id, customer_id=customer_id, domain=domain, verification_token=secrets.token_hex(16))
    session.add(row)
    session.flush()
    return row


def _domain(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, domain_id: uuid.UUID) -> CustomerEmailDomain:
    if customer_id is not None:
        customer(session, tenant_id, customer_id)
    row = session.get(CustomerEmailDomain, domain_id)
    if row is None or row.tenant_id != tenant_id or row.customer_id != customer_id:
        raise SignOnError("Domain not found.")
    return row


def txt_records(name: str) -> list[str]:
    """The TXT records at a name (tests replace this)."""
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0
    try:
        answer = resolver.resolve(name, "TXT")
    except (dns.exception.DNSException, OSError):
        return []
    return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]


def verify_domain(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, domain_id: uuid.UUID) -> CustomerEmailDomain:
    """Looks for `openclear-verification=<token>` in a TXT record at the domain, or at
    _openclear.<domain>."""
    row = _domain(session, tenant_id, customer_id, domain_id)
    expected = TXT_PREFIX + row.verification_token
    found = [r.strip() for name in (row.domain, f"_openclear.{row.domain}") for r in txt_records(name)]
    if expected not in found:
        raise SignOnError(f"No TXT record “{expected}” at {row.domain} yet. DNS changes can take a while to appear.")
    row.verified_at = row.verified_at or _now()
    session.flush()
    return row


def remove_domain(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, domain_id: uuid.UUID) -> str:
    row = _domain(session, tenant_id, customer_id, domain_id)
    name = row.domain
    session.delete(row)
    session.flush()
    return name


def may_create_person(session: Session, conn: SsoConnection, email: str) -> bool:
    """Whether this connection may create someone new with this email: always for one the bank
    set up; for a customer's own, only in the customer's verified domains."""
    if conn.managed_by != MANAGED_BY_CUSTOMER:
        return True
    return verified_domain(session, conn.tenant_id, conn.customer_id, email)
