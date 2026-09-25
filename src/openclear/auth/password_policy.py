# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Password complexity policy: a tenant-wide baseline (domain/tenant.py) that a customer
(domain/customer.py) may only ever add to, never weaken. The guarantee is structural, not
just validated: `effective_policy` combines the two with max()/OR, so the result can never
be weaker than the tenant's own policy regardless of what's stored on the customer row.

Enforced wherever a password is set: services/user_service.py::create_user_with_membership
(first set, against that one membership's policy) and change_own_password (against
`strictest` of every policy the user is subject to — one User's single password is shared
by all their memberships, so it must satisfy the tightest of them). Admin-issued temporary
passwords (admin_reset_password) are generated to satisfy it too."""

from dataclasses import dataclass

from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    min_length: int
    require_uppercase: bool
    require_lowercase: bool
    require_number: bool
    require_symbol: bool


def effective_policy(tenant: Tenant, customer: Customer | None) -> PasswordPolicy:
    min_length = tenant.password_min_length
    if customer is not None and customer.password_min_length is not None:
        min_length = max(min_length, customer.password_min_length)
    return PasswordPolicy(
        min_length=min_length,
        require_uppercase=tenant.password_require_uppercase or bool(customer and customer.password_require_uppercase),
        require_lowercase=tenant.password_require_lowercase or bool(customer and customer.password_require_lowercase),
        require_number=tenant.password_require_number or bool(customer and customer.password_require_number),
        require_symbol=tenant.password_require_symbol or bool(customer and customer.password_require_symbol),
    )


def strictest(policies: list[PasswordPolicy]) -> PasswordPolicy | None:
    """Combines several policies (one per membership) the same max()/OR way
    effective_policy combines a tenant's and a customer's — the result satisfies every
    input. None for an empty list (a user with no active memberships)."""
    if not policies:
        return None
    return PasswordPolicy(
        min_length=max(p.min_length for p in policies),
        require_uppercase=any(p.require_uppercase for p in policies),
        require_lowercase=any(p.require_lowercase for p in policies),
        require_number=any(p.require_number for p in policies),
        require_symbol=any(p.require_symbol for p in policies),
    )


def describe(policy: PasswordPolicy) -> str:
    """A short, positive-phrased summary for UI hint text, e.g. "at least 8 characters,
    an uppercase letter, and a number". Only ever shown as a hint (see users/new.html,
    users/access.html) — the server-side validate_password() above is what's actually
    authoritative."""
    parts = [f"at least {policy.min_length} characters"]
    if policy.require_uppercase:
        parts.append("an uppercase letter")
    if policy.require_lowercase:
        parts.append("a lowercase letter")
    if policy.require_number:
        parts.append("a number")
    if policy.require_symbol:
        parts.append("a symbol")
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def validate_password(password: str, policy: PasswordPolicy) -> None:
    """Raises ValueError listing every unmet requirement in one message, matching this
    app's convention of routers re-rendering a form with error=str(exc)."""
    problems = []
    if len(password) < policy.min_length:
        problems.append(f"be at least {policy.min_length} characters")
    if policy.require_uppercase and not any(c.isupper() for c in password):
        problems.append("include an uppercase letter")
    if policy.require_lowercase and not any(c.islower() for c in password):
        problems.append("include a lowercase letter")
    if policy.require_number and not any(c.isdigit() for c in password):
        problems.append("include a number")
    if policy.require_symbol and not any(not c.isalnum() for c in password):
        problems.append("include a symbol")
    if problems:
        raise ValueError("Password must " + "; ".join(problems) + ".")
