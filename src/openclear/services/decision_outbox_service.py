# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The decision outbox (SDK_PLAN.md P3, X9): a bank's new decisions written to a file on its
own schedule, for a core that takes files rather than calling the API.

- **Settings** (Settings → Decision files): on or off, the layout (OpenClear CSV, a NACHA
  return file, or one a plugin provides and the bank turned on), how often, and each
  layout's settings (a NACHA file needs the bank's routing number).
- **A run** writes every decision after the last batch, at most MAX_BATCH, to storage
  (`decision_files` area), records a batch with its SHA-256, and sends the
  `decision_batch.ready` webhook. Nothing new, no file. The batches are a cursor of their
  own, separate from the pull feed's consumers.
- **The core acknowledges** a batch once it has processed it
  (`POST /api/v1/decision-batches/{id}/acknowledge`); that also moves the `outbox`
  consumer of the decision feed, so its lag shows with the others.

A run happens when due (workers/tasks.py::decision_outbox_job, opt-in scheduler), or on
demand (`POST /api/v1/decision-batches`)."""

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.domain.decision_outbox import DecisionBatch, DecisionOutbox

logger = logging.getLogger(__name__)

MAX_BATCH = 10_000
CONSUMER = "outbox"


class OutboxError(ValueError):
    """Refused; the message is fit to show."""


def get_outbox(session: Session, tenant_id: uuid.UUID) -> DecisionOutbox:
    outbox = session.execute(select(DecisionOutbox).where(DecisionOutbox.tenant_id == tenant_id)).scalar_one_or_none()
    if outbox is None:
        outbox = DecisionOutbox(tenant_id=tenant_id, enabled=False, layout="openclear_csv", interval_minutes=60, layout_settings={})
        session.add(outbox)
        session.flush()
    return outbox


def layout_settings(session: Session, tenant_id: uuid.UUID, layout: str) -> dict[str, str]:
    outbox = session.execute(select(DecisionOutbox).where(DecisionOutbox.tenant_id == tenant_id)).scalar_one_or_none()
    return dict(((outbox.layout_settings or {}) if outbox else {}).get(layout) or {})


def feed_custom_fields(session: Session, tenant_id: uuid.UUID, settings: dict) -> list[str]:
    """The custom fields a layout's settings ask for, leaving out any since archived away entirely
    (a field is archived, not deleted, so normally every key still reads)."""
    from openclear.services import custom_field_service, decision_feed_service

    keys = decision_feed_service.custom_fields_setting(settings)
    try:
        return custom_field_service.output_keys(session, tenant_id, ",".join(keys))
    except custom_field_service.CustomFieldError:
        return []


def save(
    session: Session, tenant_id: uuid.UUID, *, enabled: bool, layout: str, interval_minutes: int, settings: dict[str, str],
) -> DecisionOutbox:
    from openclear.services import plugin_service

    try:
        chosen = plugin_service.require_enabled(session, tenant_id, "decision_layouts", layout, what="decision layout")
    except ValueError as exc:
        raise OutboxError(str(exc)) from None
    if not 5 <= interval_minutes <= 24 * 60:
        raise OutboxError("Write a file every 5 minutes to once a day (1,440 minutes).")
    values = {}
    for field in chosen.config_fields:
        value = (settings.get(field.name) or "").strip()
        if field.required and enabled and not value:
            raise OutboxError(f"{field.label} is required for this layout.")
        values[field.name] = value
    if values.get("custom_fields"):
        from openclear.services import custom_field_service

        try:
            values["custom_fields"] = ", ".join(custom_field_service.output_keys(session, tenant_id, values["custom_fields"]))
        except custom_field_service.CustomFieldError as exc:
            raise OutboxError(str(exc)) from None
    outbox = get_outbox(session, tenant_id)
    outbox.enabled, outbox.layout, outbox.interval_minutes = enabled, layout, interval_minutes
    outbox.layout_settings = {**(outbox.layout_settings or {}), layout: values}
    session.flush()
    return outbox


def _lock_key(tenant_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"openclear.decision_outbox:{tenant_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _last_sequence(session: Session, tenant_id: uuid.UUID) -> int:
    return session.execute(select(func.max(DecisionBatch.to_sequence)).where(DecisionBatch.tenant_id == tenant_id)).scalar_one() or 0


def is_due(outbox: DecisionOutbox, now: datetime) -> bool:
    if not outbox.enabled:
        return False
    if outbox.last_run_at is None:
        return True
    last = outbox.last_run_at if outbox.last_run_at.tzinfo else outbox.last_run_at.replace(tzinfo=timezone.utc)
    return now - last >= timedelta(minutes=outbox.interval_minutes)


def run(session: Session, tenant_id: uuid.UUID, *, layout: str | None = None) -> DecisionBatch | None:
    """Writes the decisions since the last batch to a file. Returns the batch, or None if
    there was nothing new."""
    from openclear import storage
    from openclear.networks.ach.returns_file import ReturnsFileError
    from openclear.services import decision_feed_service, plugin_service, webhook_service

    # One writer per bank at a time (FULL_REVIEW_PLAN.md FR-D3): the scheduled run and an API
    # request at once would each read the same last batch and write the same decisions twice.
    # The (tenant_id, from_sequence) constraint is the backstop on every database.
    if session.get_bind().dialect.name == "postgresql":
        session.execute(select(func.pg_advisory_xact_lock(_lock_key(tenant_id))))
    outbox = get_outbox(session, tenant_id)
    layout = layout or outbox.layout
    try:
        chosen = plugin_service.require_enabled(session, tenant_id, "decision_layouts", layout, what="decision layout")
    except ValueError as exc:
        raise OutboxError(str(exc)) from None
    outbox.last_run_at = datetime.now(timezone.utc)
    after = _last_sequence(session, tenant_id)
    settings = layout_settings(session, tenant_id, layout)
    feed = decision_feed_service.read_feed(session, tenant_id, after=after, limit=MAX_BATCH,
                                           custom_fields=feed_custom_fields(session, tenant_id, settings))
    if not feed:
        session.flush()
        return None
    try:
        content = chosen.write(feed, settings)
    except ReturnsFileError as exc:
        raise OutboxError(str(exc)) from None
    first, last = feed[0]["sequence"], feed[-1]["sequence"]
    filename = f"decisions-{first}-{last}.{chosen.extension}"
    ref = storage.put("decision_files", f"{tenant_id}/{uuid.uuid4().hex}-{filename}", content, read_only=True)
    batch = DecisionBatch(
        tenant_id=tenant_id, layout=layout, from_sequence=first, to_sequence=last, decision_count=len(feed), filename=filename,
        media_type=chosen.media_type, storage_ref=ref, sha256=hashlib.sha256(content).hexdigest(), size=len(content),
    )
    session.add(batch)
    session.flush()
    webhook_service.emit(session, tenant_id, "decision_batch.ready", {
        "batch_id": batch.id, "from_sequence": first, "to_sequence": last, "count": len(feed), "layout": layout,
    })
    return batch


def list_batches(session: Session, tenant_id: uuid.UUID, *, unacknowledged_only: bool, limit: int, offset: int) -> tuple[list[DecisionBatch], int]:
    query = select(DecisionBatch).where(DecisionBatch.tenant_id == tenant_id)
    if unacknowledged_only:
        query = query.where(DecisionBatch.acknowledged_at.is_(None))
    total = session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = session.execute(query.order_by(DecisionBatch.from_sequence).offset(offset).limit(limit)).scalars().all()
    return list(rows), total


def get_batch(session: Session, tenant_id: uuid.UUID, batch_id: uuid.UUID) -> DecisionBatch | None:
    batch = session.get(DecisionBatch, batch_id)
    return batch if batch is not None and batch.tenant_id == tenant_id else None


def acknowledge(session: Session, tenant_id: uuid.UUID, batch: DecisionBatch, *, user_id: uuid.UUID | None) -> DecisionBatch:
    """Marks the batch processed, and moves the feed's `outbox` consumer forward to the end of
    the latest contiguous acknowledged batch."""
    from openclear.services import decision_feed_service

    if batch.acknowledged_at is None:
        batch.acknowledged_at, batch.acknowledged_by_user_id = datetime.now(timezone.utc), user_id
        session.flush()
    oldest_open = session.execute(
        select(func.min(DecisionBatch.from_sequence)).where(DecisionBatch.tenant_id == tenant_id, DecisionBatch.acknowledged_at.is_(None))
    ).scalar_one()
    acked_through = session.execute(
        select(func.max(DecisionBatch.to_sequence)).where(
            DecisionBatch.tenant_id == tenant_id, DecisionBatch.acknowledged_at.is_not(None),
            *([DecisionBatch.to_sequence < oldest_open] if oldest_open is not None else []),
        )
    ).scalar_one()
    if acked_through:
        current = decision_feed_service.consumers(session, tenant_id)
        mine = next((c for c in current if c["consumer"] == CONSUMER), None)
        if mine is None or mine["through_sequence"] < acked_through:
            decision_feed_service.acknowledge(session, tenant_id, consumer=CONSUMER, through_sequence=acked_through, user_id=user_id)
    return batch


def run_due(session: Session, now: datetime | None = None) -> int:
    """The scheduler's tick: runs each bank's outbox that's due. Returns how many files were
    written. Bank by bank, bound to each (decision_outbox is under row-level security)."""
    from openclear.db.tenancy import bind_session_to_tenant
    from openclear.domain.tenant import Tenant

    now = now or datetime.now(timezone.utc)
    written = 0
    for tenant_id in session.execute(select(Tenant.id).where(Tenant.is_active.is_(True))).scalars().all():
        bind_session_to_tenant(session, tenant_id)
        outbox = session.execute(select(DecisionOutbox).where(DecisionOutbox.tenant_id == tenant_id)).scalar_one_or_none()
        if outbox is None or not is_due(outbox, now):
            session.rollback()
            continue
        try:
            if run(session, tenant_id) is not None:
                written += 1
            session.commit()
        except Exception:  # noqa: BLE001 -- one bank's bad settings mustn't stop the others
            session.rollback()
            logger.exception("Decision outbox failed for bank %s", tenant_id)
    return written
