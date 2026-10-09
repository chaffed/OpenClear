# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Webhooks (SDK_PLAN.md P0): events sent to a bank's systems as they happen, in the
Standard Webhooks format (https://www.standardwebhooks.com/).

- **Endpoints** are set up by a bank admin (Admin → Webhooks): a URL, the event types it
  wants, and a signing secret (`whsec_...`, shown once).
- **`emit()`** is called where something happens (an exception created, a decision
  made, a bulk upload finished). It writes one delivery per subscribed endpoint, in the
  caller's transaction, and queues a background job to send it. So an event goes out only
  for a change that committed, and isn't lost if the process stops before sending.
- **Payloads are thin:** the event type, a timestamp and ids. A receiver fetches the details
  through the API with its own credentials, so a misconfigured endpoint can't leak data.
- **Every request is signed:** `webhook-id` (the same on every retry, for de-duplication),
  `webhook-timestamp`, and `webhook-signature: v1,<base64 HMAC-SHA256 of
  "{id}.{timestamp}.{body}">` with the endpoint's secret.
- **A non-2xx response or no response is retried** on Svix's schedule (5 s, 5 min,
  30 min, 2 h, 5 h, 10 h, 10 h), about a day in all, then marked failed. An admin can
  replay a failed delivery.
- **The URL must be public https,** and it's checked again at connection time (no
  redirects followed), against server-side request forgery. A single-bank install whose
  receivers are on its own network turns on `webhook_allow_private_hosts`.
"""

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.auth.crypto import decrypt_secret, encrypt_secret
from openclear.auth.outbound_http import UnsafeUrlError, check_url, public_only_client
from openclear.config import get_settings
from openclear.domain.webhook import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint

DELIVER_JOB_KIND = "webhook.deliver"

EVENT_TYPES = {
    "exception.created": "A presented check or ACH debit raised an exception to review",
    "exception.decided": "An exception was decided, pay or return (it's in the decision feed)",
    "bulk_upload.completed": "A bulk upload finished importing (or failed)",
    "decision_batch.ready": "A decision file is ready in the outbox (services/decision_outbox_service.py)",
}
TEST_EVENT = "webhook.test"

# Svix's schedule: after the first try, wait this long before each retry.
RETRY_SCHEDULE = (
    timedelta(seconds=5), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2),
    timedelta(hours=5), timedelta(hours=10), timedelta(hours=10),
)
_SESSION_CACHE_KEY = "webhook_endpoints_by_tenant"


class WebhookError(ValueError):
    """Bad input; the message is fit to show."""


def _new_secret() -> str:
    return "whsec_" + base64.b64encode(secrets.token_bytes(24)).decode()


def _check(url: str) -> str:
    try:
        return check_url((url or "").strip(), what="webhook URL", allow_private=get_settings().webhook_allow_private_hosts)
    except UnsafeUrlError as exc:
        raise WebhookError(str(exc)) from None


def _event_types(event_types) -> list[str]:
    chosen = sorted({e for e in (event_types or []) if e})
    unknown = [e for e in chosen if e not in EVENT_TYPES]
    if unknown:
        raise WebhookError(f"Unknown event types: {', '.join(unknown)}")
    if not chosen:
        raise WebhookError("Choose at least one event type.")
    return chosen


def list_endpoints(session: Session, tenant_id: uuid.UUID) -> list[WebhookEndpoint]:
    return list(session.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id).order_by(WebhookEndpoint.created_at)
    ).scalars())


def get_endpoint(session: Session, tenant_id: uuid.UUID, endpoint_id: uuid.UUID) -> WebhookEndpoint | None:
    endpoint = session.get(WebhookEndpoint, endpoint_id)
    return endpoint if endpoint is not None and endpoint.tenant_id == tenant_id else None


def create_endpoint(
    session: Session, tenant_id: uuid.UUID, *, url: str, event_types, description: str | None = None,
    created_by_user_id: uuid.UUID | None = None,
) -> tuple[WebhookEndpoint, str]:
    secret = _new_secret()
    endpoint = WebhookEndpoint(
        tenant_id=tenant_id, url=_check(url), event_types=_event_types(event_types),
        description=(description or "").strip()[:200] or None, secret_encrypted=encrypt_secret(secret),
        created_by_user_id=created_by_user_id, is_active=True,
    )
    session.add(endpoint)
    session.flush()
    session.info.pop(_SESSION_CACHE_KEY, None)
    return endpoint, secret


def update_endpoint(
    session: Session, tenant_id: uuid.UUID, endpoint_id: uuid.UUID, *, url: str, event_types, description: str | None,
    is_active: bool,
) -> WebhookEndpoint:
    endpoint = get_endpoint(session, tenant_id, endpoint_id)
    if endpoint is None:
        raise WebhookError("Webhook not found.")
    endpoint.url, endpoint.event_types = _check(url), _event_types(event_types)
    endpoint.description = (description or "").strip()[:200] or None
    endpoint.is_active = is_active
    session.flush()
    session.info.pop(_SESSION_CACHE_KEY, None)
    return endpoint


def rotate_secret(session: Session, tenant_id: uuid.UUID, endpoint_id: uuid.UUID) -> tuple[WebhookEndpoint, str]:
    endpoint = get_endpoint(session, tenant_id, endpoint_id)
    if endpoint is None:
        raise WebhookError("Webhook not found.")
    secret = _new_secret()
    endpoint.secret_encrypted = encrypt_secret(secret)
    session.flush()
    return endpoint, secret


def recent_deliveries(session: Session, tenant_id: uuid.UUID, endpoint_id: uuid.UUID, limit: int = 50) -> list[WebhookDelivery]:
    return list(session.execute(
        select(WebhookDelivery).where(WebhookDelivery.tenant_id == tenant_id, WebhookDelivery.endpoint_id == endpoint_id)
        .order_by(WebhookDelivery.created_at.desc()).limit(limit)
    ).scalars())


# --- Emitting events -------------------------------------------------------------------------


def _subscribers(session: Session, tenant_id: uuid.UUID) -> list[WebhookEndpoint]:
    # Cached for the session: a bulk import emits an event per row, and endpoints rarely change.
    cache = session.info.setdefault(_SESSION_CACHE_KEY, {})
    if tenant_id not in cache:
        cache[tenant_id] = list(session.execute(
            select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.is_active.is_(True))
        ).scalars())
    return cache[tenant_id]


def _queue(session: Session, endpoint: WebhookEndpoint, event_type: str, data: dict, *, message_id: str | None = None) -> WebhookDelivery:
    from openclear.workers.jobs import enqueue

    delivery = WebhookDelivery(
        tenant_id=endpoint.tenant_id, endpoint_id=endpoint.id, message_id=message_id or f"msg_{uuid.uuid4().hex}",
        event_type=event_type,
        payload={"type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), "data": data},
        status=WebhookDeliveryStatus.PENDING, attempts=0,
    )
    session.add(delivery)
    session.flush()
    enqueue(session, DELIVER_JOB_KIND, tenant_id=endpoint.tenant_id, payload={"delivery_id": str(delivery.id)})
    return delivery


def emit(session: Session, tenant_id: uuid.UUID, event_type: str, data: dict) -> int:
    """Queues `event_type` for every active endpoint of this bank subscribed to it, in the
    caller's transaction. Returns how many deliveries were queued."""
    from openclear.services.plugin_service import queue_channel_events

    endpoints = [e for e in _subscribers(session, tenant_id) if event_type in (e.event_types or [])]
    serializable = {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in data.items()}
    for endpoint in endpoints:
        _queue(session, endpoint, event_type, serializable)
    # Notification channels from plugins get the same events (PLUGIN_PLAN.md PL3).
    queue_channel_events(session, tenant_id, event_type, serializable)
    return len(endpoints)


def send_test(session: Session, tenant_id: uuid.UUID, endpoint_id: uuid.UUID) -> WebhookDelivery:
    endpoint = get_endpoint(session, tenant_id, endpoint_id)
    if endpoint is None:
        raise WebhookError("Webhook not found.")
    return _queue(session, endpoint, TEST_EVENT, {"endpoint_id": str(endpoint.id)})


def replay(session: Session, tenant_id: uuid.UUID, delivery_id: uuid.UUID) -> WebhookDelivery:
    """Sends a delivery again from the start, keeping its webhook-id so a receiver that did
    process it after all can tell."""
    from openclear.workers.jobs import enqueue

    delivery = session.get(WebhookDelivery, delivery_id)
    if delivery is None or delivery.tenant_id != tenant_id:
        raise WebhookError("Delivery not found.")
    delivery.status, delivery.attempts, delivery.next_attempt_at = WebhookDeliveryStatus.PENDING, 0, None
    session.flush()
    enqueue(session, DELIVER_JOB_KIND, tenant_id=tenant_id, payload={"delivery_id": str(delivery.id)})
    return delivery


# --- Sending -------------------------------------------------------------------------------------


def sign(secret: str, message_id: str, timestamp: int, body: bytes) -> str:
    """The Standard Webhooks signature header value."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = f"{message_id}.{timestamp}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


def deliver(session: Session, delivery_id: uuid.UUID) -> None:
    """The `webhook.deliver` job: one attempt, then either done, failed for good, or the
    next attempt queued for later."""
    from openclear.workers.jobs import enqueue

    delivery = session.get(WebhookDelivery, delivery_id)
    if delivery is None or delivery.status != WebhookDeliveryStatus.PENDING:
        return
    endpoint = session.get(WebhookEndpoint, delivery.endpoint_id)
    now = datetime.now(timezone.utc)
    delivery.attempts += 1
    if endpoint is None or not endpoint.is_active:
        delivery.status, delivery.last_error = WebhookDeliveryStatus.FAILED, "The endpoint is turned off."
        session.flush()
        return

    settings = get_settings()
    body = json.dumps(delivery.payload, separators=(",", ":")).encode()
    timestamp = int(now.timestamp())
    headers = {
        "content-type": "application/json",
        "user-agent": "OpenClear-Webhooks",
        "webhook-id": delivery.message_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": sign(decrypt_secret(endpoint.secret_encrypted), delivery.message_id, timestamp, body),
    }
    try:
        _check(endpoint.url)
        with public_only_client(allow_private=settings.webhook_allow_private_hosts, timeout=settings.webhook_timeout_seconds) as client:
            response = client.post(endpoint.url, content=body, headers=headers)
        delivery.last_status_code, delivery.last_error = response.status_code, None
        succeeded = 200 <= response.status_code < 300
        if not succeeded:
            delivery.last_error = f"HTTP {response.status_code}"
    except Exception as exc:  # noqa: BLE001 -- any failure to deliver is retried, never raised
        delivery.last_status_code, delivery.last_error, succeeded = None, f"{type(exc).__name__}: {exc}"[:1000], False

    if succeeded:
        delivery.status, delivery.delivered_at, delivery.next_attempt_at = WebhookDeliveryStatus.SUCCEEDED, now, None
    elif delivery.attempts > len(RETRY_SCHEDULE):
        delivery.status, delivery.next_attempt_at = WebhookDeliveryStatus.FAILED, None
    else:
        delivery.next_attempt_at = now + RETRY_SCHEDULE[delivery.attempts - 1]
        enqueue(session, DELIVER_JOB_KIND, tenant_id=delivery.tenant_id, payload={"delivery_id": str(delivery.id)},
                run_after=delivery.next_attempt_at)
    session.flush()
