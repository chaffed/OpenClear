# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Offboarding a customer: ending its service on its last service day, and deleting its data
customer_data_retention_days (90) later (plans/CUSTOMER_OFFBOARDING_PLAN.md). Positive pay isn't
the bank's system of record (CO-D1): the core and its cold-storage reports are.

Two bank administrators schedule it (services/change_kinds.py: customer.offboard); the customer's
people are emailed to take their export. The daily housekeeping run (sweep_all) ends the service
when the last day has passed (the customer inactive, its people's sessions ended, files naming its
accounts refused) and deletes its data once the retention has passed, unless it's on legal hold.

What's deleted is found through the schema's foreign keys from the customer row: every row that
refers to it, directly or through its accounts, items, exceptions and uploads, so a table added
later is covered. The bank's action log and security events are kept: they're the bank's own
chained, signed records. A signed certificate of what was deleted stays with the bank."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.base import Base
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.bank_deletion import CustomerDeletion
from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant

# Never deleted with a customer: the bank's own records, and what isn't the customer's. The
# bank's attestations and access reviews about the customer are the bank's compliance evidence
# and outlive it, unlinked from the customer (REVIEW_1_33_PLAN.md O8, RV-D6).
_KEPT_TABLES = frozenset({"tenant", "user", "audit_log_entry", "security_event", "customer_deletion", "bank_deletion",
                          "cuec_attestation", "access_review", "access_review_item"})
# Columns holding a stored file's key, by table: the file goes with the row.
_FILE_COLUMNS = {"check_image": ("front_image_path", "back_image_path"), "bulk_upload_file": ("storage_path",),
                 "data_export_job": ("archive_path",)}
_CHUNK = 500


class CustomerOffboardingError(ValueError):
    """Fit to show."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def retention_days() -> int:
    return get_settings().customer_data_retention_days


def deletion_due(customer: Customer) -> datetime | None:
    """When its data goes: the retention after the end of its last service day."""
    end = _aware(customer.service_ended_at) or _aware(customer.service_ends_at)
    return end + timedelta(days=retention_days()) if end else None


def _customer(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
    customer = session.get(Customer, customer_id)
    if customer is None or customer.tenant_id != tenant_id:
        raise CustomerOffboardingError("No such customer.")
    return customer


def _record(session: Session, customer: Customer, action: str, summary: str, *, user_id: uuid.UUID | None,
            metadata: dict | None = None) -> None:
    from openclear.services import audit_log_service

    audit_log_service.record_action(session, customer.tenant_id, actor_user_id=user_id, channel="web" if user_id else "system",
                                    action=action, summary=summary[:500], resource_type="customer", resource_id=customer.id,
                                    metadata=metadata)


def _zone(session: Session, tenant_id: uuid.UUID) -> ZoneInfo:
    """The bank's time zone, else the installation's default, as everywhere else (cutoff_service)."""
    from openclear.services.cutoff_service import time_zone_for

    return ZoneInfo(time_zone_for(session.get(Tenant, tenant_id)))


def bank_today(session: Session, tenant_id: uuid.UUID, *, now: datetime | None = None) -> date:
    """Today at the bank: a last service day is a day in the bank's zone, not the server's."""
    return (now or _now()).astimezone(_zone(session, tenant_id)).date()


def _end_of_day(session: Session, tenant_id: uuid.UUID, day: date) -> datetime:
    return datetime.combine(day + timedelta(days=1), time(0), tzinfo=_zone(session, tenant_id)).astimezone(timezone.utc)


def _people(session: Session, customer: Customer) -> list:
    from openclear.domain.tenant_membership import TenantMembership
    from openclear.domain.user import User

    return list(session.execute(select(User).join(TenantMembership, TenantMembership.user_id == User.id).where(
        TenantMembership.tenant_id == customer.tenant_id, TenantMembership.customer_id == customer.id).distinct()).scalars())


def _tell(session: Session, customer: Customer, last_day: date) -> list[str]:
    """Emails the customer's active people its last service day, whatever their notification settings."""
    from openclear.domain.notification import Notification, NotificationChannel, NotificationType
    from openclear.domain.tenant_membership import TenantMembership
    from openclear.domain.user import User
    from openclear.services.notification_service import _render_body

    bank = session.get(Tenant, customer.tenant_id)
    people = session.execute(select(User).join(TenantMembership, TenantMembership.user_id == User.id).where(
        TenantMembership.tenant_id == customer.tenant_id, TenantMembership.customer_id == customer.id,
        TenantMembership.is_active.is_(True), User.is_active.is_(True)).distinct()).scalars().all()
    context = {"customer_name": customer.name, "bank_name": bank.name, "last_day": last_day,
               "deleted_on": last_day + timedelta(days=retention_days() + 1)}
    for user in people:
        session.add(Notification(
            tenant_id=customer.tenant_id, recipient_user_id=user.id, notification_type=NotificationType.SERVICE_ENDING,
            channel=NotificationChannel.EMAIL, destination=user.email,
            subject=f"Positive pay for {customer.name} ends on {last_day.isoformat()}"[:255],
            body=_render_body(NotificationType.SERVICE_ENDING, NotificationChannel.EMAIL, context),
            resource_type="customer", resource_id=customer.id))
    return sorted(u.email for u in people)


# --- Scheduling, stopping and holding --------------------------------------------------------


def schedule(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, last_day: date, *, user_id: uuid.UUID | None) -> Customer:
    """Sets the last service day (today or later) and tells the customer's people."""
    customer = _customer(session, tenant_id, customer_id)
    if customer.service_ended_at is not None:
        raise CustomerOffboardingError(f"{customer.name}'s service already ended.")
    ends_at = _end_of_day(session, tenant_id, last_day)
    if ends_at <= _now():
        raise CustomerOffboardingError("The last service day can't be in the past.")
    customer.service_ends_at = ends_at
    session.flush()
    told = _tell(session, customer, last_day)
    _record(session, customer, "customer.offboard.schedule",
            f"Positive pay for {customer.name} ends on {last_day.isoformat()}; its data is deleted "
            f"{retention_days()} days later. Emailed: {', '.join(told) or 'nobody (no active people)'}",
            user_id=user_id, metadata={"last_service_day": last_day.isoformat(), "emailed": told})
    return customer


def stop(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, *, user_id: uuid.UUID | None) -> Customer:
    """Cancels offboarding; if the service already ended, restores it (two administrators then)."""
    customer = _customer(session, tenant_id, customer_id)
    restored = customer.service_ended_at is not None
    customer.service_ends_at = None
    if restored:
        customer.service_ended_at, customer.is_active = None, True
    _record(session, customer, "customer.offboard.stop",
            f"{'Restored positive pay for' if restored else 'Stopped offboarding'} {customer.name}", user_id=user_id)
    return customer


def set_hold(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID, held: bool, *, reason: str | None,
             user_id: uuid.UUID | None) -> Customer:
    customer = _customer(session, tenant_id, customer_id)
    if held and not (reason or "").strip():
        raise CustomerOffboardingError("Say why the customer's data is on hold.")
    customer.legal_hold_reason = (reason or "").strip()[:500] if held else None
    _record(session, customer, "customer.legal_hold." + ("on" if held else "off"),
            f"{'Put' if held else 'Took'} {customer.name}'s data {'on' if held else 'off'} legal hold"
            + (f": {customer.legal_hold_reason}" if held else ""), user_id=user_id)
    return customer


# --- Ending the service ----------------------------------------------------------------------


def ended(customer: Customer | None, *, now: datetime | None = None) -> bool:
    """Service has ended: the daily run has recorded it, or the last day is over and the run
    hasn't come yet (REVIEW_1_33_PLAN.md O5, RV-D5). Every refusal reads this, so service ends at
    midnight at the bank, not at the next run."""
    if customer is None:
        return False
    if customer.service_ended_at is not None:
        return True
    ends = _aware(customer.service_ends_at)
    return ends is not None and ends <= (now or _now())


def last_service_day(session: Session, customer: Customer) -> date | None:
    """The last day, as the bank counts days: the one that ended when `service_ends_at` struck."""
    end = _aware(customer.service_ends_at) or _aware(customer.service_ended_at)
    return (end.astimezone(_zone(session, customer.tenant_id)) - timedelta(seconds=1)).date() if end else None


def end_service(session: Session, customer: Customer) -> None:
    """The last service day has passed: no sign-in, no sessions; files naming its accounts are
    refused (services/account_service.py), and tellers are told it's not on positive pay. The
    end is recorded as the moment the last day ended, not when this ran, so the retention clock
    and the pages agree with the day the customer was told."""
    from openclear.services import session_service

    customer.service_ended_at, customer.is_active = _aware(customer.service_ends_at) or _now(), False
    # Their sessions at this bank only: someone who also works elsewhere stays signed in there (REVIEW_1_33_PLAN.md O11).
    people = [u.id for u in _people(session, customer)]
    session_service.end_sessions(session, session_service.active_sessions(session, customer.tenant_id, people))
    _record(session, customer, "customer.offboard.ended",
            f"Positive pay for {customer.name} ended; its data is deleted on or after "
            f"{deletion_due(customer).date().isoformat()}", user_id=None)


# --- Deleting --------------------------------------------------------------------------------


def _chunks(ids: Iterable) -> Iterable[list]:
    ids = list(ids)
    for start in range(0, len(ids), _CHUNK):
        yield ids[start:start + _CHUNK]


def _collect(session: Session, customer: Customer) -> dict[str, set]:
    """Every row that refers to the customer, directly or through rows that do, by table."""
    tables = [t for t in Base.metadata.sorted_tables if t.name not in _KEPT_TABLES and "id" in t.columns]
    found: dict[str, set] = {"customer": {customer.id}}
    queue = [("customer", {customer.id})]
    while queue:
        parent, ids = queue.pop()
        for table in tables:
            for fk in table.foreign_keys:
                if fk.column.table.name != parent or fk.column.name != "id":
                    continue
                new: set = set()
                for chunk in _chunks(ids):
                    stmt = select(table.c.id).where(fk.parent.in_(chunk))
                    if "tenant_id" in table.columns:
                        stmt = stmt.where(table.c.tenant_id == customer.tenant_id)
                    new.update(session.execute(stmt).scalars())
                new -= found.get(table.name, set())
                if new:
                    found.setdefault(table.name, set()).update(new)
                    queue.append((table.name, new))
    return found


def _unlink_kept(session: Session, customer: Customer) -> dict[str, int]:
    """The kept tables' references to the customer are cleared, so its row can go and the
    records stay as the bank's own. Returns the rows unlinked per table."""
    from sqlalchemy import update

    unlinked: dict[str, int] = {}
    for name in sorted(_KEPT_TABLES):
        table = Base.metadata.tables.get(name)
        if table is None:
            continue
        for fk in table.foreign_keys:
            if fk.column.table.name == "customer" and fk.column.name == "id" and fk.parent.nullable:
                count = session.execute(update(table).where(fk.parent == customer.id).values({fk.parent.name: None})).rowcount or 0
                if count:
                    unlinked[name] = unlinked.get(name, 0) + count
    return unlinked


def _files(session: Session, found: dict[str, set]) -> list[str]:
    paths: list[str] = []
    for name, columns in _FILE_COLUMNS.items():
        table = Base.metadata.tables[name]
        for chunk in _chunks(found.get(name, ())):
            for row in session.execute(select(*(table.c[c] for c in columns)).where(table.c.id.in_(chunk))):
                paths.extend(p for p in row if p)
    return paths


@dataclass
class Preview:
    rows: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.rows.values())


def preview(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> Preview:
    customer = _customer(session, tenant_id, customer_id)
    return Preview({name: len(ids) for name, ids in sorted(_collect(session, customer).items())})


def check_can_delete(customer: Customer, *, now: datetime | None = None) -> None:
    if customer.legal_hold_reason:
        raise CustomerOffboardingError(f"{customer.name}'s data is on legal hold ({customer.legal_hold_reason}).")
    if customer.service_ended_at is None:
        raise CustomerOffboardingError(f"{customer.name}'s service hasn't ended.")
    if (now or _now()) < deletion_due(customer):
        raise CustomerOffboardingError(f"{customer.name}'s data is deleted from {deletion_due(customer).date().isoformat()}.")


def delete_data(session: Session, customer: Customer) -> CustomerDeletion:
    """Deletes the customer's data and keeps a signed certificate. The bank must be bound.
    Commits nothing."""
    from openclear import storage
    from openclear.domain.audit_log_entry import AuditLogEntry
    from openclear.services import evidence_service
    from openclear.services.offboarding_service import BACKUPS_NOTE, _forget

    check_can_delete(customer)
    tenant_id, customer_id = customer.tenant_id, customer.id
    people = [u.id for u in _people(session, customer)]
    found = _collect(session, customer)
    paths = _files(session, found)
    _record(session, customer, "customer.offboard.delete",
            f"Deleting {customer.name}'s data: {sum(len(v) for v in found.values())} rows, {len(paths)} files", user_id=None)
    session.flush()
    unlinked = _unlink_kept(session, customer)
    rows: dict[str, int] = {}
    for table in reversed(Base.metadata.sorted_tables):
        ids = found.get(table.name)
        if not ids:
            continue
        for chunk in _chunks(ids):
            rows[table.name] = rows.get(table.name, 0) + (session.execute(delete(table).where(table.c.id.in_(chunk))).rowcount or 0)
    session.flush()
    from openclear.db.tenancy import memberships_elsewhere
    from openclear.domain.tenant_membership import TenantMembership
    from openclear.domain.user import User

    def belongs_elsewhere(user_id: uuid.UUID) -> bool:
        """Another customer or the bank's staff here (this bank is bound), or another bank."""
        here = session.execute(select(TenantMembership.id).where(TenantMembership.tenant_id == tenant_id,
                                                                 TenantMembership.user_id == user_id).limit(1)).first()
        return here is not None or memberships_elsewhere(session, user_id, tenant_id) > 0

    alone = [uid for uid in people if session.get(User, uid) is not None and not session.get(User, uid).platform_role
             and not belongs_elsewhere(uid)]
    people_deleted, still_referenced = _forget(session, alone)
    last = session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant_id)
                           .order_by(AuditLogEntry.sequence.desc()).limit(1)).scalar_one_or_none()
    completed = _now()
    bundle = {
        "type": "OpenClear customer data deletion certificate",
        "bank": {"id": str(tenant_id), "name": session.get(Tenant, tenant_id).name},
        "customer": {"id": str(customer_id), "number": customer.customer_number, "name": customer.name},
        "service_ended_at": _aware(customer.service_ended_at).isoformat(),
        "retention_days": retention_days(),
        "completed_at": completed.isoformat(),
        "rows_deleted": dict(sorted(rows.items())),
        "rows_deleted_total": sum(rows.values()),
        "files_deleted": len(paths),
        "people_deleted": people_deleted,
        "people_kept_because_they_belong_elsewhere_or_are_named_in_records": len(people) - len(alone) + still_referenced,
        "people_anonymised_because_they_are_named_in_records": still_referenced,
        "rows_kept_unlinked_from_the_customer": dict(sorted(unlinked.items())),
        "kept": "The bank's action log and security events, which record what was done for the customer and this "
                "deletion; the bank's attestations and access reviews about the customer, no longer naming it; this certificate.",
        "action_log_last_entry": {"sequence": last.sequence, "entry_hash": last.entry_hash} if last else None,
        "backups": BACKUPS_NOTE,
    }
    signed = evidence_service.sign(bundle)
    record = CustomerDeletion(tenant_id=tenant_id, customer_id=customer_id, customer_number=customer.customer_number,
                              customer_name=customer.name, service_ended_at=customer.service_ended_at, completed_at=completed,
                              certificate=signed, certificate_sha256=signed["signature"]["sha256"])
    session.add(record)
    session.flush()
    for path in paths:
        storage.delete(path)
    return record


# --- The daily run ---------------------------------------------------------------------------


def sweep(session: Session, tenant_id: uuid.UUID, *, now: datetime | None = None) -> tuple[int, int]:
    """One bank: ends the service of customers whose last day has passed, and deletes the data of
    those whose retention has. Commits per customer. Returns (ended, deleted)."""
    import logging

    now = now or _now()
    bind_session_to_tenant(session, tenant_id)
    ended_count = deleted_count = 0
    for customer in session.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.service_ends_at.is_not(None),
                                                           Customer.service_ended_at.is_(None), Customer.service_ends_at <= now)).scalars().all():
        end_service(session, customer)
        session.commit()
        ended_count += 1
    for customer in session.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.service_ended_at.is_not(None),
                                                           Customer.legal_hold_reason.is_(None))).scalars().all():
        if now < deletion_due(customer):
            continue
        try:
            delete_data(session, customer)
            session.commit()
            deleted_count += 1
        except Exception:  # noqa: BLE001 -- one customer's failure doesn't stop the others; retried tomorrow
            session.rollback()
            bind_session_to_tenant(session, tenant_id)
            logging.getLogger(__name__).exception("Deleting customer %s's data failed", customer.id)
    return ended_count, deleted_count


def sweep_all(session: Session) -> None:
    bind_session_to_tenant(session, None)
    banks = session.execute(select(Tenant.id).where(Tenant.data_deleted_at.is_(None))).scalars().all()
    for tenant_id in banks:
        sweep(session, tenant_id)
    bind_session_to_tenant(session, None)


def deletions(session: Session, tenant_id: uuid.UUID, limit: int = 50) -> list[CustomerDeletion]:
    return list(session.execute(select(CustomerDeletion).where(CustomerDeletion.tenant_id == tenant_id)
                                .order_by(CustomerDeletion.completed_at.desc()).limit(limit)).scalars())
