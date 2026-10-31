# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The bank's open items, as a file it can work from without OpenClear (CONTINUITY_PLAN.md BC-D5).

Every hour, each bank's open and pending items are written as a CSV into its storage
(`data_exports/<bank>/continuity/open-items-latest.csv`) with a signed sidecar; once a day, at the
bank's end of day, a dated copy is kept, and emailed to the incident contacts when the bank asks.
It's what CUEC B-CP-2-01's plan works from when OpenClear is unreachable: the bank can decide by
hand and send returns to its core before the midnight deadline."""

import csv
import hashlib
import io
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear import storage
from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.account import Account
from openclear.domain.customer import Customer
from openclear.domain.tenant import Tenant

logger = logging.getLogger(__name__)

AREA = "data_exports"
LATEST = "open-items-latest.csv"
COLUMNS = ("customer_number", "customer_name", "account_number", "network", "item", "amount", "presented", "problems", "status",
           "recommended", "decide_by", "return_by", "default_at_cutoff")
EMAIL_ROWS = 200


@dataclass(frozen=True)
class Snapshot:
    as_of: datetime
    rows: int
    sha256: str
    ref: str
    dated_ref: str | None
    emailed: list[str]


def _key(tenant_id: uuid.UUID, name: str) -> str:
    return f"{tenant_id}/continuity/{name}"


def _zone(tenant: Tenant) -> ZoneInfo:
    from openclear.services.cutoff_service import time_zone_for

    try:
        return ZoneInfo(time_zone_for(tenant))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _local(value: datetime | None, zone: ZoneInfo) -> str:
    if value is None:
        return ""
    value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return value.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def rows_for(session: Session, tenant: Tenant) -> list[dict]:
    """Every open or pending item, with what a person deciding by hand needs. The bank is bound."""
    from openclear.networks.registry import get_adapter
    from openclear.services import exception_service
    from openclear.services.auto_disposition_service import get_disposition_mode

    zone = _zone(tenant)
    items = exception_service.list_exceptions(session, tenant.id, statuses=exception_service.NEEDS_ATTENTION)
    by_network: dict[str, list[uuid.UUID]] = {}
    for item in items:
        by_network.setdefault(item.network_code, []).append(item.source_item_id)
    sources = {}
    for code, ids in by_network.items():
        sources.update(get_adapter(code).load_source_items(session, ids))
    accounts = {a.id: a for a in session.execute(select(Account).where(
        Account.id.in_({getattr(s, "account_id", None) for s in sources.values()} - {None}))).scalars()} if sources else {}
    customers = {c.id: c for c in session.execute(select(Customer).where(
        Customer.id.in_({i.customer_id for i in items} - {None}))).scalars()} if items else {}
    modes: dict[tuple, str] = {}
    out = []
    for item in items:
        source = sources.get(item.source_item_id)
        account = accounts.get(getattr(source, "account_id", None))
        customer = customers.get(item.customer_id)
        if item.customer_id is not None:
            modes.setdefault((item.customer_id, item.network_code),
                             get_disposition_mode(session, tenant.id, item.customer_id, item.network_code).value)
        if item.network_code == "check":
            label, amount, presented = getattr(source, "check_number", ""), getattr(source, "presented_amount", ""), getattr(source, "presented_date", "")
        else:
            label, amount, presented = getattr(source, "trace_number", "") or getattr(source, "originator_name", ""), getattr(source, "amount", ""), \
                getattr(source, "effective_date", "")
        out.append({
            "customer_number": customer.customer_number if customer else "", "customer_name": customer.name if customer else "(the bank's own)",
            "account_number": account.account_number if account else "", "network": item.network_code, "item": str(label),
            "amount": str(amount), "presented": str(presented or ""), "problems": item.exception_types, "status": item.status.value,
            "recommended": item.recommended_outcome or "", "decide_by": _local(item.decision_deadline, zone),
            "return_by": _local(item.return_deadline, zone), "default_at_cutoff": modes.get((item.customer_id, item.network_code), ""),
        })
    return out


def _csv(rows: list[dict]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in ("=", "+", "@", "\t", "\r") else v) for k, v in row.items()})
    return out.getvalue().encode()


def write(session: Session, tenant: Tenant, now: datetime | None = None) -> Snapshot:
    """The latest file and its signature; the dated copy at the bank's end of day. The bank is bound; commits nothing."""
    from openclear.services import evidence_service

    now = now or datetime.now(timezone.utc)
    zone = _zone(tenant)
    rows = rows_for(session, tenant)
    data = _csv(rows)
    digest = hashlib.sha256(data).hexdigest()
    signed = evidence_service.sign({"type": "OpenClear open-items snapshot", "bank": {"id": str(tenant.id), "slug": tenant.slug},
                                    "as_of": now.isoformat(), "rows": len(rows), "sha256": digest, "columns": list(COLUMNS)})
    ref = storage.put(AREA, _key(tenant.id, LATEST), data)
    storage.put(AREA, _key(tenant.id, "open-items-latest.sig.json"), json.dumps(signed, indent=2).encode())
    dated_ref, emailed = None, []
    local = now.astimezone(zone)
    dated_name = f"open-items-{local:%Y-%m-%d}.csv"
    if local.hour >= 23 and not storage.exists(storage.ref(AREA, _key(tenant.id, dated_name))):
        dated_ref = storage.put(AREA, _key(tenant.id, dated_name), data)
        storage.put(AREA, _key(tenant.id, dated_name.replace(".csv", ".sig.json")), json.dumps(signed, indent=2).encode())
        if tenant.continuity_snapshot_emailed:
            emailed = _email(session, tenant, rows, local)
    return Snapshot(now, len(rows), digest, ref, dated_ref, emailed)


def _email(session: Session, tenant: Tenant, rows: list[dict], local: datetime) -> list[str]:
    from openclear.domain.notification import Notification, NotificationChannel, NotificationType
    from openclear.services import incident_service
    from openclear.services.notification_service import _render_body

    to = incident_service.recipients(session, tenant.id)
    context = {"bank_name": tenant.name, "as_of": local.strftime("%Y-%m-%d %H:%M"), "zone": local.tzinfo.key, "count": len(rows),
               "shown": rows[:EMAIL_ROWS], "more": max(0, len(rows) - EMAIL_ROWS), "columns": COLUMNS}
    for address in to:
        session.add(Notification(tenant_id=tenant.id, recipient_user_id=None, notification_type=NotificationType.CONTINUITY_SNAPSHOT,
                                 channel=NotificationChannel.EMAIL, destination=address,
                                 subject=f"{tenant.name}: {len(rows)} open positive pay items as of {local:%Y-%m-%d}"[:255],
                                 body=_render_body(NotificationType.CONTINUITY_SNAPSHOT, NotificationChannel.EMAIL, context),
                                 resource_type="tenant", resource_id=tenant.id))
    session.flush()
    return to


def latest(tenant_id: uuid.UUID) -> dict | None:
    """The latest snapshot's signed description (as_of, rows, sha256), or None."""
    raw = storage.read_key(AREA, _key(tenant_id, "open-items-latest.sig.json"))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def read(tenant_id: uuid.UUID, name: str = LATEST) -> bytes | None:
    if not (name == LATEST or (name.startswith("open-items-") and name.endswith(".csv") and len(name) == len("open-items-2026-10-31.csv"))):
        return None
    return storage.read_key(AREA, _key(tenant_id, name))


def dated(tenant_id: uuid.UUID) -> list[str]:
    """The dated copies' file names, newest first."""
    prefix = _key(tenant_id, "open-items-20")
    return sorted((k.rsplit("/", 1)[-1] for k in storage.list_keys(AREA, prefix) if k.endswith(".csv")), reverse=True)


def run_all(session: Session, now: datetime | None = None) -> int:
    """Every active bank, each bound in turn and committed on its own."""
    bind_session_to_tenant(session, None)
    banks = session.execute(select(Tenant.id).where(Tenant.is_active.is_(True), Tenant.data_deleted_at.is_(None))).scalars().all()
    written = 0
    for tenant_id in banks:
        bind_session_to_tenant(session, tenant_id)
        try:
            write(session, session.get(Tenant, tenant_id), now)
            session.commit()
            written += 1
        except Exception:  # noqa: BLE001 -- one bank's failure doesn't stop the others
            session.rollback()
            logger.exception("Continuity snapshot failed for bank %s", tenant_id)
    bind_session_to_tenant(session, None)
    return written
