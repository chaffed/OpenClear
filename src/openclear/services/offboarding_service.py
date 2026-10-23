# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Offboarding a bank: deleting its data when it leaves, with a signed certificate
(plans/OFFBOARDING_PLAN.md; GLBA 12 CFR 364 App. B II.B.4, III.C.4).

The safeguards (OB-D2, OB-D8 to OB-D13): two operators to start offboarding and two to delete;
the bank told, and a wait (bank_offboarding_wait_days) for its final export; deletion refused
without a full export taken since offboarding started, unless the request gives a reason; the
operator types the bank's slug; the approver sees a preview of exactly what goes; the job waits
bank_deletion_delay_hours after approval and can still be cancelled; a legal hold stops it; the
demo bank and sandboxes are refused; every step is a critical security event and in the
provider's signed log; and a signed certificate of what was deleted, which says the host's
backups still hold copies until they expire.

The job (DELETE_JOB_KIND) has no bank, so deleting the bank's jobs doesn't delete it. It wipes
every row with the bank's tenant_id (the schema's own order, so a table added later is covered),
its files and the people who belong nowhere else, and keeps the bank's row inactive so the slug
is never reused. Every step can run again, so a failed job is simply retried."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.db.base import Base
from openclear.db.tenancy import acting_as_tenant, bind_session_to_tenant
from openclear.domain.bank_deletion import BankDeletion
from openclear.domain.tenant import Tenant

DELETE_JOB_KIND = "bank.delete"
# The storage areas a bank's files live in (storage.AREAS), each under the bank's id.
FILE_AREAS = ("check_images", "bulk_uploads", "tenant_assets", "data_exports", "audit_archives")
# Kept on the bank's row after deletion; every other column goes back to its default.
_KEPT_COLUMNS = frozenset({"id", "slug", "name", "created_at", "is_demo", "is_sandbox", "offboarding_started_at",
                           "data_deleted_at"})
# Each step's security event (services/security_events.py), for the installation's SIEM and the bank's.
_EVENTS = {
    "bank.offboard.start": "bank.offboarding", "bank.offboard.cancel": "bank.offboarding",
    "bank.delete.approve": "bank.offboarding", "bank.delete.done": "bank.offboarding",
    "bank.legal_hold.on": "bank.legal_hold.changed", "bank.legal_hold.off": "bank.legal_hold.changed",
}
BACKUPS_NOTE = ("Copies remain in the host's backups until they expire on the host's backup retention schedule "
                "(CUEC H-CP-9-01); they aren't restored except to recover the whole installation.")


class OffboardingError(ValueError):
    """Fit to show."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def wait_days() -> int:
    return get_settings().bank_offboarding_wait_days


def delay_hours() -> int:
    return get_settings().bank_deletion_delay_hours


def deletable_from(bank: Tenant) -> datetime | None:
    """When the bank's data may be deleted: the end of the wait, or None if offboarding hasn't started."""
    started = _aware(bank.offboarding_started_at)
    return started + timedelta(days=wait_days()) if started else None


def _event(session: Session, bank: Tenant, action: str, summary: str, *, user_id: uuid.UUID | None, metadata: dict | None = None) -> None:
    """In the provider's signed log, and, while the bank still exists, in its action log, so a
    critical security event reaches both the installation's SIEM and the bank's."""
    from openclear.services import audit_log_service, platform_log_service, security_events

    platform_log_service.record(session, actor_user_id=user_id, action=f"platform.{action}", summary=summary[:500],
                                resource_type="tenant", resource_id=bank.id)
    security_events.emit(session, _EVENTS[action], tenant_id=None, actor_type="user" if user_id else "system", actor_id=user_id,
                         target_type="tenant", target_id=bank.id, target_label=bank.name,
                         details={"action": action, "summary": summary[:300]}, channel="platform")
    if bank.data_deleted_at is None:
        with acting_as_tenant(session, bank.id):
            audit_log_service.record_action(session, bank.id, actor_user_id=user_id, channel="system", action=action,
                                            summary=summary[:500], resource_type="tenant", resource_id=bank.id, metadata=metadata)


def _bank(session: Session, bank_id: uuid.UUID) -> Tenant:
    bank = session.get(Tenant, bank_id)
    if bank is None:
        raise OffboardingError("No such bank.")
    if bank.is_demo or bank.is_sandbox:
        raise OffboardingError("The demo bank and sandboxes are wiped on their own schedule, not offboarded.")
    if bank.data_deleted_at is not None:
        raise OffboardingError(f"{bank.name}'s data was already deleted.")
    return bank


# --- Starting, cancelling and holding --------------------------------------------------------


def start(session: Session, bank_id: uuid.UUID, *, user_id: uuid.UUID | None) -> Tenant:
    """Starts the wait, and tells the bank to take its final export: emailed to its incident
    contacts and shown to its administrators until one acknowledges it (services/incident_service.py)."""
    from openclear.services import incident_service

    bank = _bank(session, bank_id)
    if bank.offboarding_started_at is not None:
        return bank
    bank.offboarding_started_at = _now()
    session.flush()
    when = deletable_from(bank).date().isoformat()
    incident_service.issue(
        session, kind="offboarding", title=f"{bank.name}'s data will be deleted on or after {when}",
        message=(f"Your service provider has started offboarding {bank.name} from OpenClear. On or after {when} every "
                 "item, image, decision, setting and person at your bank will be deleted, and your action log with them. "
                 "Before then, an administrator should take a final data export (Settings → Data export) and keep it with "
                 "the bank's records. You'll receive a signed certificate of the deletion. If this is a mistake, contact "
                 "your service provider now."),
        tenant_ids=[bank.id], user_id=user_id, bank_data_affected=True)
    _event(session, bank, "bank.offboard.start", f"Started offboarding {bank.name}: its data can be deleted from {when}",
           user_id=user_id)
    return bank


def cancel(session: Session, bank_id: uuid.UUID, *, user_id: uuid.UUID | None) -> Tenant:
    """Stops offboarding, and any deletion still waiting to run. One operator: it keeps data."""
    bank = _bank(session, bank_id)
    waiting = session.execute(select(BankDeletion).where(BankDeletion.bank_id == bank.id, BankDeletion.status == "queued")).scalars().all()
    for record in waiting:
        record.status, record.last_error = "cancelled", "Cancelled before it ran."
    if bank.offboarding_started_at is None and not waiting:
        return bank
    bank.offboarding_started_at = None
    _event(session, bank, "bank.offboard.cancel", f"Stopped offboarding {bank.name}" + (" and its waiting deletion" if waiting else ""),
           user_id=user_id)
    return bank


def set_hold(session: Session, bank_id: uuid.UUID, held: bool, *, reason: str | None, user_id: uuid.UUID | None) -> Tenant:
    """A legal hold (a regulator's request, litigation): deletion is refused while it's on.
    Putting it on is one operator; taking it off is a two-person change (platform.bank.hold)."""
    bank = _bank(session, bank_id)
    if held and not (reason or "").strip():
        raise OffboardingError("Say why the bank's data is on hold.")
    bank.legal_hold_reason = (reason or "").strip()[:500] if held else None
    _event(session, bank, "bank.legal_hold." + ("on" if held else "off"),
           f"{'Put' if held else 'Took'} {bank.name}'s data {'on' if held else 'off'} legal hold" + (f": {bank.legal_hold_reason}" if held else ""),
           user_id=user_id)
    return bank


# --- Deleting -------------------------------------------------------------------------------


@dataclass
class Preview:
    """What a deletion would remove, for the request and its approver to see."""

    rows: dict[str, int]
    people_deleted: list[str]
    people_kept: list[str]
    final_export_at: datetime | None

    @property
    def total(self) -> int:
        return sum(self.rows.values())


def _final_export_at(session: Session, bank: Tenant) -> datetime | None:
    """The bank's last full data export since offboarding started, if any. Read as the bank: the
    platform's session and the job's have none bound, and row-level security would hide it."""
    from openclear.domain.data_export_job import DataExportJob, DataExportJobStatus

    started = _aware(bank.offboarding_started_at)
    if started is None:
        return None
    with acting_as_tenant(session, bank.id):
        done = session.execute(select(DataExportJob.completed_at).where(
            DataExportJob.tenant_id == bank.id, DataExportJob.customer_id.is_(None), DataExportJob.status == DataExportJobStatus.COMPLETED,
            DataExportJob.completed_at.is_not(None)).order_by(DataExportJob.completed_at.desc()).limit(1)).scalar_one_or_none()
    done = _aware(done)
    return done if done is not None and done >= started else None


def _people(session: Session, bank_id: uuid.UUID) -> tuple[list, list]:
    """The bank's people: those who'd be deleted (no other bank, no platform role) and those kept."""
    from openclear.db.tenancy import memberships_elsewhere
    from openclear.domain.tenant_membership import TenantMembership
    from openclear.domain.user import User

    with acting_as_tenant(session, bank_id):
        ids = session.execute(select(TenantMembership.user_id).where(TenantMembership.tenant_id == bank_id).distinct()).scalars().all()
    gone, kept = [], []
    for user in (session.get(User, i) for i in ids):
        if user is None:
            continue
        (kept if user.platform_role or memberships_elsewhere(session, user.id, bank_id) else gone).append(user)
    return gone, kept


def preview(session: Session, bank_id: uuid.UUID) -> Preview:
    bank = _bank(session, bank_id)
    with acting_as_tenant(session, bank.id):
        rows = {}
        for table in Base.metadata.sorted_tables:
            if "tenant_id" in table.columns and table.name != "tenant":
                count = session.execute(select(func.count()).select_from(table).where(table.c.tenant_id == bank.id)).scalar_one()
                if count:
                    rows[table.name] = count
    gone, kept = _people(session, bank.id)
    return Preview(dict(sorted(rows.items())), sorted(u.email for u in gone), sorted(u.email for u in kept),
                   _final_export_at(session, bank))


def check_can_delete(session: Session, bank_id: uuid.UUID, *, override_reason: str | None = None, now: datetime | None = None) -> Tenant:
    bank = _bank(session, bank_id)
    if bank.legal_hold_reason:
        raise OffboardingError(f"{bank.name}'s data is on legal hold ({bank.legal_hold_reason}); take it off hold first.")
    due = deletable_from(bank)
    if due is None:
        raise OffboardingError(f"Start offboarding {bank.name} first: the bank is told, and has {wait_days()} days for its final export.")
    if (now or _now()) < due:
        raise OffboardingError(f"{bank.name}'s data can be deleted from {due.date().isoformat()}, {wait_days()} days after "
                               "offboarding started.")
    if _final_export_at(session, bank) is None and not (override_reason or "").strip():
        raise OffboardingError(f"{bank.name} hasn't taken a full data export since offboarding started. Ask it to, or say why "
                               "its data should be deleted without one (for example, the bank declined in writing).")
    return bank


def queue_deletion(session: Session, bank_id: uuid.UUID, *, requested_by: uuid.UUID | None, approved_by: uuid.UUID | None,
                   confirm_slug: str, override_reason: str | None = None) -> BankDeletion:
    """The second step, once approved: queues the job to run after bank_deletion_delay_hours,
    still cancellable until then."""
    from openclear.workers import jobs

    bank = check_can_delete(session, bank_id, override_reason=override_reason)
    if (confirm_slug or "").strip().lower() != bank.slug:
        raise OffboardingError(f"Type the bank's slug, {bank.slug}, to confirm.")
    existing = session.execute(select(BankDeletion).where(BankDeletion.bank_id == bank.id, BankDeletion.status == "queued")).scalar_one_or_none()
    if existing is not None:
        return existing
    runs_at = _now() + timedelta(hours=delay_hours())
    record = BankDeletion(bank_id=bank.id, status="queued", requested_by_user_id=requested_by, approved_by_user_id=approved_by,
                          requested_at=_now(), runs_at=runs_at, override_reason=(override_reason or "").strip()[:500] or None)
    session.add(record)
    session.flush()
    jobs.enqueue(session, DELETE_JOB_KIND, tenant_id=None, payload={"deletion_id": str(record.id)},
                 dedupe_key=f"bank.delete:{record.id}", run_after=runs_at)
    _event(session, bank, "bank.delete.approve",
           f"Approved deleting {bank.name}'s data; it runs at {runs_at.isoformat(timespec='minutes')} unless cancelled"
           + (f". Without a final export: {record.override_reason}" if record.override_reason else ""), user_id=approved_by)
    return record


def _delete_files(bank_id: uuid.UUID, model_paths: list[str]) -> None:
    """Last, once the rows are gone: files can't be put back if the database step fails, but a
    retried job deletes them again."""
    from openclear import storage

    for path in model_paths:
        storage.delete(path)
    for area in FILE_AREAS:
        storage.delete_tenant(area, bank_id)


# What's a person's own, and goes with them: their sign-in material, messages and links to identity
# providers. Every other reference to a person is a record of what they did (the action log,
# decisions, approvals), which keeps them.
_PERSONAL = (("email_change", "user_id"), ("email_change", "requested_by_user_id"), ("idempotency_record", "user_id"),
             ("login_session", "user_id"), ("notification", "recipient_user_id"), ("notification_preference", "user_id"),
             ("password_reset", "user_id"), ("password_reset", "requested_by_user_id"), ("revoked_session", "user_id"),
             ("user_device", "user_id"), ("webauthn_challenge", "user_id"), ("webauthn_credential", "user_id"),
             ("sso_identity", "user_id"), ("scim_user", "user_id"), ("embed_launch", "user_id"))


def _forget(session: Session, user_ids: list[uuid.UUID]) -> tuple[int, int]:
    """Deletes these people and what's theirs, one at a time; someone still named in a record of
    what they did (the bank's action log, a decision, another bank's records) is kept, signed out
    for good since they belong nowhere. Returns (deleted, kept)."""
    from sqlalchemy.exc import IntegrityError

    from openclear.domain.user import User
    from openclear.services.demo_tenant_service import delete_users

    deleted = kept = 0
    for user_id in user_ids:
        savepoint = session.begin_nested()
        try:
            for name, column in _PERSONAL:
                table = Base.metadata.tables.get(name)
                if table is not None and column in table.columns:
                    session.execute(delete(table).where(table.c[column] == user_id))
            delete_users(session, User.id == user_id)
            session.flush()
            savepoint.commit()
            deleted += 1
        except IntegrityError:
            savepoint.rollback()
            user = session.get(User, user_id)
            if user is not None:
                user.is_active = False
            kept += 1
    return deleted, kept


def _wipe(session: Session, bank: Tenant) -> dict[str, int]:
    """Every row with the bank's tenant_id, children before parents. Returns the rows deleted per table."""
    deleted: dict[str, int] = {}
    for table in reversed(Base.metadata.sorted_tables):
        if "tenant_id" in table.columns and table.name != "tenant":
            count = session.execute(delete(table).where(table.c.tenant_id == bank.id)).rowcount or 0
            if count:
                deleted[table.name] = count
    session.flush()
    return deleted


def _strip(bank: Tenant) -> None:
    """The bank's row stays so its slug is never reused, with every setting back to its default."""
    for column in Tenant.__table__.columns:
        if column.key in _KEPT_COLUMNS:
            continue
        default = column.default.arg if column.default is not None and not callable(column.default.arg) else None
        setattr(bank, column.key, default)
    bank.is_active = False


def _email(session: Session, user_id: uuid.UUID | None) -> str | None:
    from openclear.domain.user import User

    user = session.get(User, user_id) if user_id else None
    return user.email if user else None


def run(session: Session, deletion_id: uuid.UUID) -> BankDeletion | None:
    """The job: deletes the bank's data and signs the certificate. Every check is made again
    here, so a hold put on, or offboarding cancelled, during the delay stops it. Commits nothing."""
    from openclear.domain.audit_log_entry import AuditLogEntry
    from openclear.domain.ml_model import MlModel
    from openclear.services import evidence_service

    record = session.get(BankDeletion, deletion_id)
    if record is None or record.status != "queued":
        return record
    try:
        bank = check_can_delete(session, record.bank_id, override_reason=record.override_reason)
    except OffboardingError as exc:
        record.status, record.last_error = "refused", str(exc)
        return record
    bind_session_to_tenant(session, bank.id)
    last = session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == bank.id)
                           .order_by(AuditLogEntry.sequence.desc()).limit(1)).scalar_one_or_none()
    entries = session.execute(select(func.count()).select_from(AuditLogEntry).where(AuditLogEntry.tenant_id == bank.id)).scalar_one()
    final_export = _final_export_at(session, bank)
    gone, kept = _people(session, bank.id)
    gone_ids = [u.id for u in gone]
    model_paths = list(session.execute(select(MlModel.artifact_path).where(MlModel.tenant_id == bank.id)).scalars())
    # The bank's own SIEM heard of it at approval (bank.delete.approve), a delay before: an event
    # raised now would be deleted with the bank's rows before it was sent.
    rows = _wipe(session, bank)
    bind_session_to_tenant(session, None)
    people_deleted, still_referenced = _forget(session, gone_ids)
    completed = _now()
    bundle = {
        "type": "OpenClear bank data deletion certificate",
        "bank": {"id": str(bank.id), "name": bank.name, "slug": bank.slug},
        "offboarding_started_at": _aware(bank.offboarding_started_at).isoformat(),
        "deletion_requested_by": _email(session, record.requested_by_user_id),
        "deletion_approved_by": _email(session, record.approved_by_user_id),
        "approved_at": _aware(record.requested_at).isoformat(),
        "completed_at": completed.isoformat(),
        "final_data_export_completed_at": final_export.isoformat() if final_export else None,
        "deleted_without_final_export_because": record.override_reason,
        "action_log": {"entries": entries, "last_sequence": last.sequence if last else None,
                       "last_entry_hash": getattr(last, "entry_hash", None) if last else None},
        "rows_deleted": dict(sorted(rows.items())),
        "rows_deleted_total": sum(rows.values()),
        "storage_areas_cleared": [*FILE_AREAS, "ml_models"],
        "people_deleted": people_deleted,
        "people_kept_because_they_belong_elsewhere_or_are_named_in_records": len(kept) + still_referenced,
        "kept": "The bank's name and slug, inactive, so the slug is never reused; this certificate; the service provider's "
                "own action log and incident notices.",
        "backups": BACKUPS_NOTE,
    }
    signed = evidence_service.sign(bundle)
    _strip(bank)
    bank.data_deleted_at = completed
    record.status, record.completed_at, record.last_error = "done", completed, None
    record.certificate, record.certificate_sha256 = signed, signed["signature"]["sha256"]
    session.flush()
    _delete_files(bank.id, [p for p in model_paths if p])
    _event(session, bank, "bank.delete.done",
           f"Deleted {bank.name}'s data: {sum(rows.values())} rows, {people_deleted} people (certificate {record.certificate_sha256[:16]})",
           user_id=record.approved_by_user_id)
    session.flush()
    return record


def latest(session: Session, bank_id: uuid.UUID) -> BankDeletion | None:
    return session.execute(select(BankDeletion).where(BankDeletion.bank_id == bank_id)
                           .order_by(BankDeletion.requested_at.desc()).limit(1)).scalar_one_or_none()
