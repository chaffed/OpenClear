# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The integration checklist (SDK_PLAN.md P4; docs/guides/certification.md): what an
integration has shown it does, read from what it actually did in its bank (usually a
sandbox), plus a webhook receiver probe run on request.

Each check is `pass`, `todo` (not seen yet) or `fail`, with what was seen. `required` checks
make up certification for a core or online banking integration; the others show the
optional parts it uses."""

import json
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.domain.api_client import ApiClient
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.conformance import ConformanceResult
from openclear.domain.idempotency_record import IdempotencyRecord
from openclear.domain.webhook import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint

PROBE = "webhook_signature_probe"


@dataclass(frozen=True)
class Check:
    key: str
    title: str
    required: bool
    status: str  # pass, todo, fail
    detail: str

    def as_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "required": self.required, "status": self.status, "detail": self.detail}


def _api_count(session: Session, tenant_id: uuid.UUID, actions: tuple[str, ...]) -> int:
    return session.execute(select(func.count()).select_from(AuditLogEntry).where(
        AuditLogEntry.tenant_id == tenant_id, AuditLogEntry.channel == "api", AuditLogEntry.action.in_(actions),
    )).scalar_one()


def checklist(session: Session, tenant_id: uuid.UUID) -> list[Check]:
    from openclear.services import decision_feed_service

    def seen(key, title, required, count, noun):
        return Check(key, title, required, "pass" if count else "todo", f"{count} {noun} through the API" if count else f"No {noun} through the API yet")

    clients = session.execute(select(ApiClient).where(ApiClient.tenant_id == tenant_id, ApiClient.revoked_at.is_(None))).scalars().all()
    used = [c for c in clients if c.last_used_at]
    checks = [
        Check("api_client", "Authenticates with an API client (OAuth client credentials)", True, "pass" if used else "todo",
              f"{len(used)} of {len(clients)} API clients have signed in" if clients else "No API client yet (Admin → API clients)"),
        seen("sync", "Syncs customers and accounts", True,
             _api_count(session, tenant_id, ("customer.create", "customer.update", "account.create", "account.update")), "customer and account changes"),
        seen("issued_items", "Sends issued checks (the positive pay file)", True, _api_count(session, tenant_id, ("issued_item.create",)), "issued checks"),
        seen("presented_items", "Sends presented items (checks or ACH debits)", True,
             _api_count(session, tenant_id, ("paid_item.create", "ach_transaction.create", "check_image.upload")), "presented items"),
    ]
    keys = session.execute(select(func.count()).select_from(IdempotencyRecord).where(IdempotencyRecord.tenant_id == tenant_id)).scalar_one()
    checks.append(Check("idempotency", "Sends an Idempotency-Key with writes, so retries are safe", True, "pass" if keys else "todo",
                        f"{keys} keyed writes in the last day" if keys else "No Idempotency-Key seen in the last day (the SDKs send one)"))

    latest = decision_feed_service.latest_sequence(session, tenant_id)
    consumers = decision_feed_service.consumers(session, tenant_id)
    caught_up = [c for c in consumers if c["through_sequence"] > 0 and c["behind"] == 0]
    if not latest:
        decided = Check("decisions", "Acts on decisions and acknowledges them (feed or files)", True, "todo",
                        "No decisions yet: decide an exception in the sandbox, then consume the feed")
    elif caught_up:
        decided = Check("decisions", "Acts on decisions and acknowledges them (feed or files)", True, "pass",
                        f"{', '.join(c['consumer'] for c in caught_up)} acknowledged through decision {latest}")
    else:
        behind = ", ".join(f"{c['consumer']} ({c['behind']} behind)" for c in consumers) or "no consumer has acknowledged anything"
        decided = Check("decisions", "Acts on decisions and acknowledges them (feed or files)", True, "todo", f"Latest decision is {latest}; {behind}")
    checks.append(decided)

    endpoints = session.execute(select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.is_active.is_(True))).scalars().all()
    delivered = session.execute(select(func.count()).select_from(WebhookDelivery).where(
        WebhookDelivery.tenant_id == tenant_id, WebhookDelivery.status == WebhookDeliveryStatus.SUCCEEDED)).scalar_one()
    checks.append(Check("webhooks", "Receives webhooks", True, "pass" if delivered else "todo",
                        f"{delivered} delivered" if delivered else ("Endpoint set up; no delivery succeeded yet (send a test from Admin → Webhooks)"
                                                                 if endpoints else "No webhook endpoint yet (Admin → Webhooks)")))
    probe = session.execute(select(ConformanceResult).where(ConformanceResult.tenant_id == tenant_id, ConformanceResult.check == PROBE)).scalar_one_or_none()
    checks.append(Check(PROBE, "Verifies webhook signatures (rejects a forged one)", True,
                        ("pass" if probe.passed else "fail") if probe else "todo", probe.detail if probe else "Run the probe below"))

    act_as = _api_count(session, tenant_id, ("api_client.act_as", "api_client.token_exchange"))
    checks.append(seen("acts_for_people", "Acts for signed-in people (online banking)", False, act_as, "actions for a person"))
    embeds = _api_count(session, tenant_id, ("embed.launch",))
    checks.append(seen("embedding", "Embeds OpenClear's pages (online banking)", False, embeds, "embedded sessions"))
    files = session.execute(select(func.count()).select_from(AuditLogEntry).where(
        AuditLogEntry.tenant_id == tenant_id, AuditLogEntry.action == "decision_batch.acknowledge")).scalar_one()
    checks.append(Check("decision_files", "Takes decision files from the outbox", False, "pass" if files else "todo",
                        f"{files} files acknowledged" if files else "Not used (fine if the feed is)"))
    return checks


def summary(checks: list[Check]) -> dict:
    required = [c for c in checks if c.required]
    return {"certified": all(c.status == "pass" for c in required), "required_passed": sum(c.status == "pass" for c in required),
            "required_total": len(required), "checks": [c.as_dict() for c in checks]}


def probe_webhooks(session: Session, tenant_id: uuid.UUID) -> ConformanceResult:
    """Sends the bank's first active webhook endpoint two test events: one correctly signed,
    which it should accept (2xx), and one with a forged signature, which it must reject
    (4xx). Records the result."""
    from openclear.auth.crypto import decrypt_secret
    from openclear.auth.outbound_http import public_only_client
    from openclear.config import get_settings
    from openclear.services import webhook_service

    endpoint = session.execute(select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.is_active.is_(True))
                               .order_by(WebhookEndpoint.created_at)).scalars().first()
    if endpoint is None:
        passed, detail = False, "No active webhook endpoint to probe (Admin → Webhooks)."
    else:
        settings = get_settings()
        secret = decrypt_secret(endpoint.secret_encrypted)
        body = json.dumps({"type": "webhook.test", "timestamp": "", "data": {"probe": True}}, separators=(",", ":")).encode()
        now = int(time.time())

        def send(signature: str, message_id: str) -> int | str:
            headers = {"content-type": "application/json", "user-agent": "OpenClear-Webhooks", "webhook-id": message_id,
                       "webhook-timestamp": str(now), "webhook-signature": signature}
            try:
                with public_only_client(allow_private=settings.webhook_allow_private_hosts, timeout=settings.webhook_timeout_seconds) as client:
                    return client.post(endpoint.url, content=body, headers=headers).status_code
            except Exception as exc:  # noqa: BLE001
                return f"{type(exc).__name__}"

        genuine_id, forged_id = f"msg_probe_{uuid.uuid4().hex}", f"msg_probe_{uuid.uuid4().hex}"
        genuine = send(webhook_service.sign(secret, genuine_id, now, body), genuine_id)
        forged = send(webhook_service.sign("whsec_" + "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB", forged_id, now, body), forged_id)
        accepted = isinstance(genuine, int) and 200 <= genuine < 300
        rejected = isinstance(forged, int) and 400 <= forged < 500
        passed = accepted and rejected
        detail = (f"{endpoint.url}: the signed event got {genuine} ({'accepted' if accepted else 'expected 2xx'}); "
                  f"the forged one got {forged} ({'rejected' if rejected else 'expected 4xx: it must be refused'})")
    result = session.execute(select(ConformanceResult).where(ConformanceResult.tenant_id == tenant_id, ConformanceResult.check == PROBE)).scalar_one_or_none()
    if result is None:
        result = ConformanceResult(tenant_id=tenant_id, check=PROBE, passed=passed, detail=detail)
        session.add(result)
    result.passed, result.detail = passed, detail
    session.flush()
    return result
