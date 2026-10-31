# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Self-service vendor sandboxes (SDK_PLAN.md P4): free, on an installation run only for
that (`sandbox_signup_enabled`). A vendor asks at /ui/sandbox and gets, at once:

- its own bank, seeded with the demo's realistic data (accounts, a business customer,
  issued and presented checks, ACH, exceptions, decisions, people in each role);
- the admin's sign-in, and an API client (Admin group, allowed to act for users), each
  secret shown once;
- `sandbox_lifetime_days` to build against it, after which housekeeping wipes it.

The sandbox's people have addresses at `<slug>.sandbox.example.com`, so no sandbox can
collide with another, or with a real address."""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.errors import UserFacingError
from openclear.config import get_settings
from openclear.domain.tenant import Tenant
from openclear.domain.user import User


class SandboxError(UserFacingError):
    pass


@dataclass(frozen=True)
class NewSandbox:
    tenant: Tenant
    admin_email: str
    admin_password: str
    client_id: str
    client_secret: str


def _domain(slug: str) -> str:
    return f"{slug}.sandbox.example.com"


def create(session: Session, *, organization: str, contact_email: str) -> NewSandbox:
    from openclear.services import api_client_service, demo_tenant_service, provisioning_service, security_group_service

    # Only on an installation that holds nothing but sandboxes (and the demo): refuses if a
    # real bank is here, in case the setting was turned on by mistake.
    real = session.execute(select(Tenant.id).where(Tenant.is_sandbox.is_(False), Tenant.is_demo.is_(False), Tenant.is_active.is_(True))).first()
    if real is not None:
        raise SandboxError("Sandboxes aren't available here.")
    organization = " ".join((organization or "").split())[:100]
    contact_email = (contact_email or "").strip().lower()[:255]
    if not organization:
        raise SandboxError("Tell us your organization's name.")
    if "@" not in contact_email or "." not in contact_email.split("@")[-1]:
        raise SandboxError("Enter an email address we can reach you at.")
    slug = f"sbx-{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(12) + "aA1!"
    identity = provisioning_service.create_tenant_with_admin(
        session, tenant_name=f"{organization} (sandbox)", tenant_slug=slug, admin_email=f"admin@{_domain(slug)}", admin_password=password,
    )
    tenant = identity.tenant
    tenant.is_sandbox = True
    tenant.sandbox_contact_email = contact_email
    tenant.sandbox_expires_at = datetime.now(timezone.utc) + timedelta(days=get_settings().sandbox_lifetime_days)
    session.commit()
    demo_tenant_service.seed_demo_content(
        session, tenant, identity.admin_user, password=password, email_domain=_domain(slug),
        customer_contact_email=f"dana@{_domain(slug)}", tenant_name=f"{organization} (sandbox)",
    )
    admin_group = security_group_service.get_security_group_by_name(session, tenant.id, "Admin")
    issued = api_client_service.create_client(
        session, tenant.id, name="Sandbox API client", security_group_id=admin_group.id, can_act_for_users=True,
    )
    session.commit()
    return NewSandbox(tenant, identity.admin_user.email, password, issued.client.client_id, issued.secret)


def expire_due(session: Session, now: datetime | None = None) -> int:
    """Wipes every sandbox past its expiry: its data, files and people. The bank row stays,
    inactive, so its slug is never reused. Returns how many were wiped."""
    from openclear.services import demo_tenant_service

    now = now or datetime.now(timezone.utc)
    due = session.execute(
        select(Tenant).where(Tenant.is_sandbox.is_(True), Tenant.is_active.is_(True), Tenant.sandbox_expires_at < now)
    ).scalars().all()
    for tenant in due:
        demo_tenant_service._wipe_tenant_children(session, tenant.id)
        domain = _domain(tenant.slug)
        demo_tenant_service.delete_users(session, User.email.like(f"%@{domain}"))
        tenant.is_active = False
        session.commit()
    return len(due)


def sandbox_of(session: Session, tenant_id: uuid.UUID) -> Tenant | None:
    tenant = session.get(Tenant, tenant_id)
    return tenant if tenant is not None and tenant.is_sandbox else None
