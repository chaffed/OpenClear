# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Whether a password sign-in must also use a security key (OWASP_PLAN.md OW-D18–D22; ADR 0004).

It's required when the membership asks for it (`require_webauthn`, for multi-customer access),
or the bank requires keys, or the membership's customer does. A bank's or customer's
requirement starts with a grace period (the bank's `security_key_grace_days`, counted from when
it was turned on), during which someone without a key can still sign in, with a warning. After
it, they register one as part of signing in. Someone who already has a key always uses it.

SSO, embedded sessions and API clients don't come through here: their second factor is the
identity provider's, the embedding host's, or not applicable (OW-D22)."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership


@dataclass(frozen=True)
class KeyRequirement:
    required: bool
    # When someone without a key can no longer sign in without registering one; None when the
    # requirement is already enforced (or not required).
    grace_ends: datetime | None = None

    @property
    def enforced(self) -> bool:
        return self.required and (self.grace_ends is None or datetime.now(timezone.utc) >= self.grace_ends)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def requirement(session: Session, tenant: Tenant, membership: TenantMembership) -> KeyRequirement:
    if membership.require_webauthn:
        return KeyRequirement(required=True)
    starts = []
    if tenant.require_security_key:
        starts.append(tenant.security_key_required_since)
    if membership.customer_id is not None:
        customer = session.get(Customer, membership.customer_id)
        if customer is not None and customer.require_security_key:
            starts.append(customer.security_key_required_since)
    if not starts:
        return KeyRequirement(required=False)
    known = [_aware(s) for s in starts if s is not None]
    if not known:  # turned on without a date (shouldn't happen): enforce
        return KeyRequirement(required=True)
    return KeyRequirement(required=True, grace_ends=min(known) + timedelta(days=tenant.security_key_grace_days))
