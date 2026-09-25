# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant
from openclear.repositories.customer_repo import CustomerRepository
from openclear.services import message_content


@dataclass(frozen=True, slots=True)
class CustomerInput:
    customer_number: str
    name: str
    external_customer_id: str | None = None
    tax_id: str | None = None
    primary_contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    notes: str | None = None


def _apply_input(customer: Customer, data: CustomerInput) -> None:
    customer.customer_number = data.customer_number
    customer.name = data.name
    customer.external_customer_id = data.external_customer_id
    customer.tax_id = data.tax_id
    customer.primary_contact_name = data.primary_contact_name
    customer.email = data.email
    customer.phone = data.phone
    customer.website = data.website
    customer.address_line1 = data.address_line1
    customer.address_line2 = data.address_line2
    customer.city = data.city
    customer.state = data.state
    customer.postal_code = data.postal_code
    customer.notes = data.notes


def create_customer(session: Session, tenant_id: uuid.UUID, data: CustomerInput) -> Customer:
    repo = CustomerRepository(session, tenant_id)
    customer = Customer()
    _apply_input(customer, data)
    repo.add(customer)
    session.flush()
    return customer


def update_customer(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, data: CustomerInput) -> Customer | None:
    customer = CustomerRepository(session, tenant_id).get(customer_id)
    if customer is None:
        return None
    _apply_input(customer, data)
    session.flush()
    return customer


def list_customers(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    limit: int | None = None,
    offset: int | None = None,
    order_by: Any = None,
) -> list[Customer]:
    return CustomerRepository(session, tenant_id).list(limit=limit, offset=offset, order_by=order_by)


def get_customer(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Customer | None:
    return CustomerRepository(session, tenant_id).get(customer_id)


def get_customer_by_number(session: Session, tenant_id: uuid.UUID, customer_number: str) -> Customer | None:
    """Used by bulk file imports (accounts, users) to resolve a human-readable customer
    number in an uploaded row to this tenant's internal customer id — files never carry
    our UUIDs, only the number a bank employee would actually recognize."""
    matches = CustomerRepository(session, tenant_id).list(customer_number=customer_number)
    return matches[0] if matches else None


def set_password_policy(
    session: Session,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    *,
    min_length: int | None,
    require_uppercase: bool,
    require_lowercase: bool,
    require_number: bool,
    require_symbol: bool,
) -> Customer | None:
    """This customer's own ADDITIONAL password requirements, on top of the tenant's own
    baseline (services/tenant_service.py::set_password_policy) — see
    auth/password_policy.py::effective_policy for how the two combine. Rejects a
    min_length weaker than the tenant's own with a clear error rather than silently
    accepting a value that would just be a no-op once combined; the boolean flags need no
    such check since OR-combining a customer's own value with the tenant's already makes
    weakening structurally impossible regardless of what's submitted."""
    customer = CustomerRepository(session, tenant_id).get(customer_id)
    if customer is None:
        return None

    if min_length is not None:
        tenant = session.get(Tenant, tenant_id)
        if min_length < tenant.password_min_length:
            raise ValueError(
                f"This customer's minimum length can't be less than the tenant's own minimum "
                f"of {tenant.password_min_length} characters. Leave it blank to just use the tenant's minimum."
            )

    customer.password_min_length = min_length
    customer.password_require_uppercase = require_uppercase
    customer.password_require_lowercase = require_lowercase
    customer.password_require_number = require_number
    customer.password_require_symbol = require_symbol
    session.flush()
    return customer


def set_banner_message(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, *, banner_message: str | None) -> Customer | None:
    """Markdown source for this customer's own slice of the persistent post-login banner
    (see Customer.banner_message's own column comment and web/templates.py::
    render_markdown, which does the actual HTML conversion at display time, not here).
    Genuinely self-service — see web/routers/customer_banner.py, gated by
    customer_banner:manage, the one permission in the catalog deliberately not masked out
    of a customer-scoped session. Blank input normalizes to None (nothing renders).
    Raises ValueError (via services/message_content.py's validation, which also
    validates/re-encodes any embedded image) if too long or the image is invalid/oversized."""
    banner_message = message_content.validate_and_normalize_message(banner_message)

    customer = CustomerRepository(session, tenant_id).get(customer_id)
    if customer is None:
        return None
    customer.banner_message = banner_message
    session.flush()
    return customer
