# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Customers, for a core or middleware keeping OpenClear in sync (SDK_PLAN.md P0): a paged
list and an upsert by the bank's own customer number. `PUT` is naturally safe to repeat."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from openclear.api.v1.pagination import Page, Since, changed_since, page_params, set_page_headers, updated_since_param
from openclear.auth.deps import require_permission
from openclear.db.session import get_db
from openclear.db.tenancy import TenantContext
from openclear.domain.customer import Customer
from openclear.repositories.customer_repo import CustomerRepository
from openclear.services import audit_log_service, customer_service

router = APIRouter(prefix="/customers", tags=["customers"])
_manage = require_permission("customer:manage")


class CustomerUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    is_active: bool = True
    external_customer_id: str | None = Field(None, max_length=64, description="The core's own id for this customer (its CIF), if different")
    tax_id: str | None = Field(None, max_length=32)
    primary_contact_name: str | None = Field(None, max_length=255)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=32)
    website: str | None = Field(None, max_length=255)
    address_line1: str | None = Field(None, max_length=255)
    address_line2: str | None = Field(None, max_length=255)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=50)
    postal_code: str | None = Field(None, max_length=20)
    notes: str | None = Field(None, max_length=1000)


class CustomerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_number: str
    name: str
    is_active: bool
    external_customer_id: str | None
    tax_id: str | None
    primary_contact_name: str | None
    email: str | None
    phone: str | None
    city: str | None
    state: str | None
    updated_at: datetime | None = None
    # Self-service (CUSTOMER_ADMIN_PLAN.md): set with PUT /customers/{number}/self-service,
    # not by the upsert, so a core's sync never changes them.
    self_manages_users: bool = False
    self_manages_sso: bool = False


class SelfService(BaseModel):
    """Whether the customer's own administrators manage its people and its sign-on."""

    manages_people: bool
    manages_sign_on: bool
    administrators: list[str] = Field([], description="Read only: the customer's people who can manage its people")


@router.get("", response_model=list[CustomerRead])
def list_customers(
    request: Request, response: Response, external_customer_id: str | None = None, page: Page = Depends(page_params),
    updated_since: Since | None = Depends(updated_since_param),
    db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage),
) -> list[CustomerRead]:
    """Oldest first, paged. Filter by the core's id with `external_customer_id`."""
    repo = CustomerRepository(db, ctx.tenant_id)
    filters = {"external_customer_id": external_customer_id} if external_customer_id else {}
    extra, order = changed_since(Customer, updated_since, (Customer.created_at, Customer.id))
    rows = repo.list(**filters, **extra, order_by=order, limit=page.limit, offset=page.offset)
    set_page_headers(request, response, page, repo.count(**filters, **extra), since=updated_since, rows=rows)
    return [CustomerRead.model_validate(c) for c in rows]


@router.put("/{customer_number}", response_model=CustomerRead, responses={201: {"model": CustomerRead}})
def upsert_customer(customer_number: str, payload: CustomerUpsert, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)):
    """Creates the customer with this number, or updates it to match. `200` when it existed,
    `201` when it was created. Fields left out are cleared, as with any `PUT`."""
    data = customer_service.CustomerInput(customer_number=customer_number, **payload.model_dump(exclude={"is_active"}))
    existing = customer_service.get_customer_by_number(db, ctx.tenant_id, customer_number)
    if existing is None:
        customer = customer_service.create_customer(db, ctx.tenant_id, data)
        action, code = "customer.create", status.HTTP_201_CREATED
    else:
        customer = customer_service.update_customer(db, ctx.tenant_id, existing.id, data)
        action, code = "customer.update", status.HTTP_200_OK
    customer.is_active = payload.is_active
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action=action,
        summary=f"{'Created' if code == 201 else 'Synced'} customer {customer_number} ({payload.name})",
        resource_type="customer", resource_id=customer.id,
    )
    db.commit()
    return JSONResponse(CustomerRead.model_validate(customer).model_dump(mode="json"), status_code=code)


def _self_service(db: Session, ctx: TenantContext, customer: Customer) -> SelfService:
    from openclear.domain.user import User
    from openclear.services import customer_people_service

    admins = [db.get(User, m.user_id).email for m in customer_people_service.administrators(db, ctx.tenant_id, customer.id)]
    return SelfService(manages_people=customer.self_manages_users, manages_sign_on=customer.self_manages_sso, administrators=admins)


def _by_number(db: Session, ctx: TenantContext, customer_number: str) -> Customer:
    from fastapi import HTTPException

    customer = customer_service.get_customer_by_number(db, ctx.tenant_id, customer_number)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.get("/{customer_number}/self-service", response_model=SelfService)
def get_self_service(customer_number: str, db: Session = Depends(get_db), ctx: TenantContext = Depends(_manage)) -> SelfService:
    """Whether this customer's own administrators manage its people and sign-on, and who they are."""
    return _self_service(db, ctx, _by_number(db, ctx, customer_number))


@router.put("/{customer_number}/self-service", response_model=SelfService)
def set_self_service(customer_number: str, payload: SelfService, db: Session = Depends(get_db),
                     ctx: TenantContext = Depends(_manage)) -> SelfService:
    """Lets the customer's own administrators manage its people and sign-on, or stops them at
    once. They can give only the roles marked offered to customers."""
    from openclear.services import customer_people_service

    customer = _by_number(db, ctx, customer_number)
    customer_people_service.set_self_service(db, ctx.tenant_id, customer.id, users=payload.manages_people, sso=payload.manages_sign_on)
    audit_log_service.record_action(
        db, ctx.tenant_id, actor_user_id=ctx.user_id, channel="api", action="customer.self_service",
        summary=f"{customer.name} manages its own people: {'yes' if payload.manages_people else 'no'}; "
                f"its own sign-on: {'yes' if payload.manages_sign_on else 'no'}",
        resource_type="customer", resource_id=customer.id,
    )
    db.commit()
    return _self_service(db, ctx, customer)
