# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Delivering security events to each destination from its own cursor (SIEM_PLAN.md S3,
decision SI-D4): in order, at least once, in batches, with retries that back off. A
destination that's down keeps its place and catches up when it recovers; nothing a
request does waits on one. Run by workers/tasks.security_event_delivery_job on a short
tick; the "Security events delivered" control reads the delivery record kept here."""

import logging
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.auth.crypto import decrypt_secret, encrypt_secret
from openclear.auth.outbound_http import UnsafeUrlError, check_url
from openclear.config import Settings, get_settings
from openclear.domain.security_event import SecurityEvent
from openclear.domain.security_event_destination import SecurityEventDestination
from openclear.domain.tenant import Tenant
from openclear.services import security_events
from openclear.services.siem import formats, senders

logger = logging.getLogger(__name__)

# After a failure, wait this long before trying again; the last step repeats. Like webhooks
# (services/webhook_service.RETRY_SCHEDULE) at first, but never giving up: a SIEM that's
# down for a day still gets every event when it's back.
RETRY_SCHEDULE = (timedelta(seconds=5), timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=5), timedelta(minutes=15))
MAX_BATCHES_PER_TICK = 20
# The control fails when a destination has been failing, or its oldest undelivered event
# has waited, this long.
STALE_AFTER = timedelta(hours=1)


class DestinationError(ValueError):
    """Bad input on the settings form; the message is fit to show."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


# --- Targets -------------------------------------------------------------------------------------


def installation_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return settings.siem_kind in senders.KINDS


def _installation_target(settings: Settings, destination: SecurityEventDestination | None = None) -> senders.Target:
    return senders.Target(
        kind=settings.siem_kind, host=settings.siem_host or None, port=settings.siem_port, transport=settings.siem_transport,
        format=settings.siem_format if settings.siem_kind == "syslog" else "json", url=settings.siem_url or None,
        token=settings.siem_token or None, splunk_index=settings.siem_splunk_index or None,
        splunk_sourcetype=settings.siem_splunk_sourcetype or None,
        channel=str(destination.id) if destination is not None else None, allow_private=True,
        ca_file=settings.siem_tls_ca_file or None, verify=settings.siem_tls_verify, timeout=settings.siem_timeout_seconds,
    )


def target_for(destination: SecurityEventDestination, settings: Settings | None = None) -> senders.Target:
    settings = settings or get_settings()
    if destination.tenant_id is None:
        return _installation_target(settings, destination)
    return senders.Target(
        kind=destination.kind, host=destination.host, port=destination.port, transport=destination.transport or "tls",
        format=destination.format, url=destination.url,
        token=decrypt_secret(destination.token_encrypted) if destination.token_encrypted else None,
        splunk_index=destination.splunk_index, splunk_sourcetype=destination.splunk_sourcetype, channel=str(destination.id),
        allow_private=settings.siem_allow_private_hosts, timeout=settings.siem_timeout_seconds,
    )


def describe(destination: SecurityEventDestination) -> str:
    """Where it sends, for the tile and the control: "syslog tls://siem.example:6514 (cef)"."""
    settings = get_settings()
    if destination.tenant_id is None:
        kind, host, port, transport, fmt, url = (settings.siem_kind, settings.siem_host, settings.siem_port, settings.siem_transport,
                                                 settings.siem_format, settings.siem_url)
    else:
        kind, host, port, transport, fmt, url = (destination.kind, destination.host, destination.port, destination.transport,
                                                 destination.format, destination.url)
    if kind == "syslog":
        return f"syslog {transport}://{host}:{port} ({fmt})"
    # Without the query string or any credentials: some collectors take a key there, and this
    # text goes in the action log.
    parts = urlsplit(url or "")
    shown = f"{parts.scheme}://{parts.hostname or ''}{f':{parts.port}' if parts.port else ''}{parts.path}" if parts.scheme else ""
    return f"{senders.KINDS.get(kind, kind)} {shown}".strip()


def sync_installation_destination(session: Session) -> SecurityEventDestination | None:
    """The installation's destination row, from configuration: created the first time one
    is configured, turned off (keeping its cursor) if the configuration is removed."""
    settings = get_settings()
    row = session.execute(select(SecurityEventDestination).where(SecurityEventDestination.tenant_id.is_(None))).scalar_one_or_none()
    if not installation_configured(settings):
        if row is not None and row.enabled:
            row.enabled = False
            session.flush()
        return row
    if row is None:
        row = SecurityEventDestination(tenant_id=None, kind=settings.siem_kind, enabled=True, format="json", cursor=0,
                                       delivered_count=0, consecutive_failures=0)
        session.add(row)
    row.kind, row.enabled = settings.siem_kind, True
    row.format = settings.siem_format if settings.siem_kind == "syslog" else "json"
    session.flush()
    return row


# --- Delivery ------------------------------------------------------------------------------------


def _pending_query(destination: SecurityEventDestination, now: datetime):
    """What's after the cursor, leaving out the last few seconds' events. Ids are handed out
    when an event is written but become visible when its transaction commits, so a slower
    transaction can commit a lower id after a higher one was delivered; the settle window
    lets requests finish first. (An event from a transaction longer than the window could
    still be passed over; it stays in the pull API and the action log.)"""
    stmt = select(SecurityEvent).where(SecurityEvent.id > destination.cursor,
                                       SecurityEvent.occurred_at <= now - timedelta(seconds=get_settings().siem_settle_seconds))
    if destination.tenant_id is not None:
        stmt = stmt.where(SecurityEvent.tenant_id == destination.tenant_id)
    return stmt


def _slugs(session: Session, events: list[SecurityEvent]) -> dict[uuid.UUID, str]:
    ids = {e.tenant_id for e in events if e.tenant_id is not None}
    if not ids:
        return {}
    return dict(session.execute(select(Tenant.id, Tenant.slug).where(Tenant.id.in_(ids))).all())


def deliver(session: Session, destination: SecurityEventDestination, *, now: datetime | None = None,
            hostname: str | None = None) -> int:
    """Sends what's pending, batch by batch, moving the cursor after each one the
    destination accepts. Stops at the first failure and schedules the retry. Returns how
    many events were delivered. The caller commits."""
    settings = get_settings()
    now = now or _now()
    hostname = hostname or socket.gethostname()
    target = target_for(destination, settings)
    delivered = 0
    for _ in range(MAX_BATCHES_PER_TICK):
        events = list(session.execute(_pending_query(destination, now).order_by(SecurityEvent.id).limit(settings.siem_batch_size)).scalars())
        if not events:
            break
        slugs = _slugs(session, events)
        records = [security_events.as_record(e, slugs.get(e.tenant_id)) for e in events]
        destination.last_attempt_at = now
        try:
            senders.send(target, records, hostname=hostname)
        except Exception as exc:  # noqa: BLE001 -- any failure keeps the cursor and is retried
            destination.last_error = (str(exc) if isinstance(exc, (senders.DeliveryError, UnsafeUrlError))
                                      else f"{type(exc).__name__}: {exc}")[:1000]
            destination.failing_since = destination.failing_since or now
            destination.consecutive_failures += 1
            destination.next_attempt_at = now + RETRY_SCHEDULE[min(destination.consecutive_failures, len(RETRY_SCHEDULE)) - 1]
            logger.warning("Security event delivery to %s failed: %s", describe(destination), destination.last_error)
            break
        destination.cursor = events[-1].id
        destination.delivered_count += len(events)
        destination.last_success_at = now
        destination.failing_since = destination.last_error = destination.next_attempt_at = None
        destination.consecutive_failures = 0
        delivered += len(events)
    session.flush()
    return delivered


def deliver_due(session: Session, *, now: datetime | None = None) -> int:
    """One tick: every enabled destination not waiting for a retry. Commits after each
    destination, so one that's down never holds up the others."""
    now = now or _now()
    sync_installation_destination(session)
    session.commit()
    rows = session.execute(select(SecurityEventDestination).where(SecurityEventDestination.enabled.is_(True))).scalars().all()
    total = 0
    for destination in rows:
        if destination.next_attempt_at is not None and _aware(destination.next_attempt_at) > now:
            continue
        try:
            total += deliver(session, destination, now=now)
            session.commit()
        except Exception:  # noqa: BLE001 -- isolate one destination's failure from the rest
            session.rollback()
            logger.exception("Security event delivery to destination %s failed", destination.id)
    return total


def send_test(destination: SecurityEventDestination, *, actor_label: str | None = None) -> None:
    """The "Send test event" button: one made-up event, sent now, not stored. Raises
    DeliveryError (or UnsafeUrlError) with a message fit to show."""
    record = {
        "id": f"evt_test_{uuid.uuid4().hex[:12]}", "sequence": 0, "time": _now().isoformat(),
        "event": {"name": "security_events.test", "category": "integration", "outcome": "success", "severity": "low"},
        "bank": {"id": str(destination.tenant_id)} if destination.tenant_id else None,
        "actor": {"type": "user", "email": actor_label} if actor_label else None,
        "details": {"message": "A test event from OpenClear. Delivery to this destination works."},
    }
    senders.send(target_for(destination), [{k: v for k, v in record.items() if v is not None}])


# --- Status (the tile and the control) -------------------------------------------------------------


@dataclass
class DeliveryStatus:
    destination: SecurityEventDestination
    label: str
    backlog: int
    oldest_pending_at: datetime | None

    @property
    def stale(self) -> bool:
        cutoff = _now() - STALE_AFTER
        failing_since = _aware(self.destination.failing_since)
        oldest = _aware(self.oldest_pending_at)
        return (failing_since is not None and failing_since < cutoff) or (oldest is not None and oldest < cutoff)


def status_for_bank(session: Session, tenant_id: uuid.UUID) -> list[DeliveryStatus]:
    """The destinations that receive this bank's events: its own and the installation's,
    each with how many of the bank's events wait to go."""
    rows = session.execute(select(SecurityEventDestination).where(
        SecurityEventDestination.enabled.is_(True),
        (SecurityEventDestination.tenant_id == tenant_id) | SecurityEventDestination.tenant_id.is_(None),
    )).scalars().all()
    result = []
    for destination in rows:
        if destination.tenant_id is None and not installation_configured():
            continue
        pending = select(func.count(), func.min(SecurityEvent.occurred_at)).where(
            SecurityEvent.id > destination.cursor, SecurityEvent.tenant_id == tenant_id)
        backlog, oldest = session.execute(pending).one()
        label = "The installation's destination" if destination.tenant_id is None else "Your destination"
        result.append(DeliveryStatus(destination, label, backlog, oldest))
    return result


def counts_by_category(session: Session, tenant_id: uuid.UUID, start: datetime, end: datetime) -> dict[str, int]:
    rows = session.execute(select(SecurityEvent.category, func.count()).where(
        SecurityEvent.tenant_id == tenant_id, SecurityEvent.occurred_at >= start, SecurityEvent.occurred_at < end,
    ).group_by(SecurityEvent.category)).all()
    return {category: count for category, count in sorted(rows)}


# --- A bank's own destination -------------------------------------------------------------------


def bank_destination(session: Session, tenant_id: uuid.UUID) -> SecurityEventDestination | None:
    return session.execute(select(SecurityEventDestination).where(SecurityEventDestination.tenant_id == tenant_id)).scalar_one_or_none()


def save_bank_destination(
    session: Session, tenant_id: uuid.UUID, *, kind: str, enabled: bool, host: str = "", port: str = "", transport: str = "tls",
    fmt: str = "json", url: str = "", token: str = "", splunk_index: str = "", splunk_sourcetype: str = "",
) -> SecurityEventDestination:
    """Creates or replaces the bank's destination. A blank token keeps the saved one."""
    settings = get_settings()
    if kind not in senders.KINDS:
        raise DestinationError("Choose where to send events.")
    destination = bank_destination(session, tenant_id)
    if destination is None:
        destination = SecurityEventDestination(tenant_id=tenant_id, cursor=0, delivered_count=0, consecutive_failures=0)
        session.add(destination)
    destination.kind, destination.enabled = kind, enabled
    try:
        if kind == "syslog":
            if transport not in senders.TRANSPORTS:
                raise DestinationError("Choose TLS, TCP or UDP.")
            if transport == "udp" and not settings.siem_allow_private_hosts:
                raise DestinationError("UDP is only for a relay on the same network; use TLS.")
            if fmt not in formats.FORMATS:
                raise DestinationError("Choose JSON, CEF or LEEF.")
            senders.check_host(host, allow_private=settings.siem_allow_private_hosts)
            try:
                port_number = int(port) if str(port).strip() else (6514 if transport == "tls" else 514)
            except ValueError:
                raise DestinationError("The port must be a number.") from None
            if not 1 <= port_number <= 65535:
                raise DestinationError("The port must be between 1 and 65535.")
            destination.host, destination.port, destination.transport, destination.format = host.strip(), port_number, transport, fmt
            destination.url = destination.splunk_index = destination.splunk_sourcetype = None
            destination.token_encrypted = None
        else:
            what = "Splunk HEC URL" if kind == "splunk_hec" else "HTTPS URL"
            destination.url = check_url(url.strip(), what=what, allow_private=settings.siem_allow_private_hosts)
            destination.host = destination.port = destination.transport = None
            destination.format = "json"
            if token.strip():
                destination.token_encrypted = encrypt_secret(token.strip())
            elif kind == "splunk_hec" and not destination.token_encrypted:
                raise DestinationError("Splunk HEC needs its token.")
            destination.splunk_index = (splunk_index.strip() or None) if kind == "splunk_hec" else None
            destination.splunk_sourcetype = (splunk_sourcetype.strip() or None) if kind == "splunk_hec" else None
    except UnsafeUrlError as exc:
        raise DestinationError(str(exc)) from None
    # A changed destination is tried at once, not after a backoff meant for the old one.
    destination.next_attempt_at, destination.failing_since, destination.consecutive_failures = None, None, 0
    destination.last_error = None
    session.flush()
    return destination


def remove_bank_destination(session: Session, tenant_id: uuid.UUID) -> bool:
    destination = bank_destination(session, tenant_id)
    if destination is None:
        return False
    session.delete(destination)
    session.flush()
    return True


def mark_feed_read(session: Session, tenant_id: uuid.UUID) -> None:
    """The pull API was read (at most one write every five minutes)."""
    tenant = session.get(Tenant, tenant_id)
    now = _now()
    if tenant is not None and (tenant.security_event_feed_read_at is None
                               or _aware(tenant.security_event_feed_read_at) < now - timedelta(minutes=5)):
        tenant.security_event_feed_read_at = now
        session.flush()
