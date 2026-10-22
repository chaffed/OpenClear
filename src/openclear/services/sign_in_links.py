# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

""""Which organization do I sign in to?" (FULL_REVIEW_PLAN.md FR8, U2). Someone at the plain
sign-in page who doesn't know their organization's short name gives their email, and OpenClear
emails that address its sign-in links, one per organization (or customer) it belongs to.

The page answers the same whatever was typed, and only the inbox's owner sees the links, so it
tells nobody which addresses have a sign-in (ASVS 5.0 6.3.8), unlike asking for the email first
and skipping the organization when there's only one."""

from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.db.tenancy import acting_as_tenant, bind_session_to_tenant
from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User


def send_links(session: Session, email: str, *, base_url: str) -> int:
    """Queues the email, if the address belongs to someone with access somewhere. Returns how
    many links it held (0: nothing sent), for tests; callers must not show it."""
    from openclear.domain.notification import NotificationType
    from openclear.services import notification_service

    email = (email or "").strip().lower()[:320]
    user = session.execute(select(User).where(func.lower(User.email) == email)).scalar_one_or_none() if email else None
    if user is None or not user.is_active or user.is_service_account:
        return 0
    bind_session_to_tenant(session, None, user_id=user.id)  # their own memberships, in every bank
    memberships = session.execute(select(TenantMembership).where(
        TenantMembership.user_id == user.id, TenantMembership.is_active.is_(True))).scalars().all()
    links: dict[str, str] = {}
    base = base_url.rstrip("/")
    for membership in memberships:
        tenant = session.get(Tenant, membership.tenant_id)
        if tenant is None or not tenant.is_active:
            continue
        if membership.customer_id is None:
            links.setdefault(f"{base}/ui/login/{tenant.slug}", tenant.name)
            continue
        with acting_as_tenant(session, tenant.id):
            customer = session.get(Customer, membership.customer_id)
        if customer is not None and customer.is_active:
            links.setdefault(f"{base}/ui/login/{tenant.slug}/{quote(customer.customer_number)}", f"{customer.name} at {tenant.name}")
    if not links:
        return 0
    notification_service._queue(
        session, tenant_id=None, recipient=user, notification_type=NotificationType.SIGN_IN_LINKS,
        template_context={"links": sorted(links.items(), key=lambda pair: pair[1])},
    )
    session.flush()
    return len(links)
