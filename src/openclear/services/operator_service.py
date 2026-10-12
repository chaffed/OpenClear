# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The platform operator's view (UX_PLAN.md U9): every bank at a glance, and provisioning a
new one from the web. Read bank by bank, each bound to its bank, so Postgres row-level
security still applies. Linking into a bank goes only through the bank's own grant of
support access (services/support_access_service.py), never from here."""

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.bulk_upload_file import BulkUploadFile
from openclear.domain.customer import Customer
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.job import Job, JobStatus
from openclear.domain.tenant import Tenant
from openclear.services.exception_service import NEEDS_ATTENTION

SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class ProvisioningError(ValueError):
    """Bad input; the message is fit to show."""


@dataclass
class BankRow:
    tenant: Tenant
    customers: int
    open_exceptions: int
    overdue: int
    jobs_queued: int
    jobs_failed: int
    last_import_at: datetime | None
    delivery: str
    delivery_tone: str


def banks(session: Session, *, include_sandboxes: bool = False, now: datetime | None = None) -> list[BankRow]:
    from openclear.services.siem import delivery

    now = now or datetime.now(timezone.utc)
    stmt = select(Tenant)
    if not include_sandboxes:
        stmt = stmt.where(Tenant.is_sandbox.is_(False))
    tenants = list(session.execute(stmt.order_by(Tenant.name)).scalars())
    open_counts = dict(session.execute(select(ExceptionItem.tenant_id, func.count()).where(
        ExceptionItem.status.in_(list(NEEDS_ATTENTION))).group_by(ExceptionItem.tenant_id)).all())
    overdue = dict(session.execute(select(ExceptionItem.tenant_id, func.count()).where(
        ExceptionItem.status.in_(list(NEEDS_ATTENTION)), ExceptionItem.decision_deadline.is_not(None),
        ExceptionItem.decision_deadline < now).group_by(ExceptionItem.tenant_id)).all())
    jobs = {}
    for tenant_id, status, count in session.execute(select(Job.tenant_id, Job.status, func.count()).where(
            Job.status.in_((JobStatus.QUEUED, JobStatus.FAILED))).group_by(Job.tenant_id, Job.status)).all():
        jobs[(tenant_id, status)] = count
    rows = []
    for tenant in tenants:
        bind_session_to_tenant(session, tenant.id)  # the per-bank reads below are under row-level security
        customers = session.execute(select(func.count()).select_from(Customer).where(Customer.tenant_id == tenant.id)).scalar_one()
        last_import = session.execute(select(func.max(BulkUploadFile.uploaded_at)).where(BulkUploadFile.tenant_id == tenant.id)).scalar_one()
        statuses = delivery.status_for_bank(session, tenant.id)
        if not statuses:
            text, tone = "Pull API only", ""
        elif any(s.stale for s in statuses):
            text, tone = "Falling behind", "danger"
        elif any(s.destination.failing_since for s in statuses):
            text, tone = "Retrying", "warn"
        else:
            text, tone = f"{sum(s.backlog for s in statuses)} waiting", "ok"
        rows.append(BankRow(tenant, customers, open_counts.get(tenant.id, 0), overdue.get(tenant.id, 0),
                            jobs.get((tenant.id, JobStatus.QUEUED), 0), jobs.get((tenant.id, JobStatus.FAILED), 0),
                            last_import, text, tone))
    return rows


def provision(session: Session, *, name: str, slug: str, admin_email: str, operator_email: str):
    """A new bank with its default groups and return reasons, and a first admin who must
    choose their own password on first sign-in. Returns (tenant, the temporary password).
    The caller commits."""
    from openclear.services import audit_log_service, provisioning_service

    name, slug, admin_email = (name or "").strip(), (slug or "").strip().lower(), (admin_email or "").strip().lower()
    if not name:
        raise ProvisioningError("The bank's name is required.")
    if not SLUG.match(slug):
        raise ProvisioningError("The short name is lowercase letters, digits and hyphens, such as first-community.")
    if session.execute(select(Tenant.id).where(Tenant.slug == slug)).first():
        raise ProvisioningError("That short name is taken.")
    if "@" not in admin_email:
        raise ProvisioningError("The first admin's email is required.")
    from openclear.domain.user import User

    if session.execute(select(User.id).where(User.email == admin_email)).first():
        raise ProvisioningError("That email already signs in to OpenClear. Use another for the first admin; "
                                "the person can be added to the bank afterwards.")
    password = secrets.token_urlsafe(12)
    try:
        identity = provisioning_service.create_tenant_with_admin(
            session, tenant_name=name, tenant_slug=slug, admin_email=admin_email, admin_password=password)
    except ValueError as exc:
        raise ProvisioningError(str(exc)) from None
    identity.admin_user.must_change_password = True
    audit_log_service.record_action(session, identity.tenant.id, actor_user_id=None, channel="system", action="tenant.provision",
                                    summary=f"Organization set up by the platform operator {operator_email}, first admin {admin_email}",
                                    resource_type="tenant", resource_id=identity.tenant.id)
    session.flush()
    return identity.tenant, password


def tenant_by_id(session: Session, tenant_id: uuid.UUID) -> Tenant | None:
    return session.get(Tenant, tenant_id)
