# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Security events (SIEM_PLAN.md S2; MASTER_PLAN.md M1): sign-ins and their failures,
lockouts, second factors, tokens, refusals, and changes to who may do what. Each is written
to `security_event` in the same transaction as what it describes, and logged.

Two ways in:
- **emit()**, called where the event happens (sign-in, tokens, refusals);
- **the action log**: an action-log entry whose action is in AUDIT_EVENTS becomes a
  security event too, linked by its sequence, so the many places that already record
  security-relevant changes need no second call (audit_log_service.record_action).

**Never in an event:** passwords, tokens, secrets, session ids, file contents, account
numbers, amounts or payees. An event says that something was done, by whom and from where.
The details stay in OpenClear."""

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.security_event import SecurityEvent

logger = logging.getLogger("openclear.security")


@dataclass(frozen=True)
class EventType:
    category: str
    severity: str  # low, medium, high, critical


CATALOG: dict[str, EventType] = {
    # authentication
    "auth.login.succeeded": EventType("authentication", "low"),
    "auth.login.failed": EventType("authentication", "medium"),
    "auth.lockout": EventType("authentication", "high"),
    "auth.mfa.succeeded": EventType("authentication", "low"),
    "auth.mfa.failed": EventType("authentication", "medium"),
    # A security key added to or removed from someone's sign-in (needs a recent sign-in).
    "auth.mfa.added": EventType("authentication", "medium"),
    "auth.mfa.removed": EventType("authentication", "medium"),
    # An administrator removed all of someone's security keys (OW-D21).
    "auth.mfa.reset": EventType("authentication", "high"),
    "auth.sso.succeeded": EventType("authentication", "low"),
    "auth.sso.failed": EventType("authentication", "medium"),
    "auth.logout": EventType("authentication", "low"),
    "auth.session.revoked": EventType("authentication", "medium"),
    # A refresh token already used was presented again: it was copied, so the session is ended
    # (services/session_service.py; FULL_REVIEW_PLAN.md FR-D8).
    "auth.session.reuse_detected": EventType("authentication", "high"),
    "auth.token.issued": EventType("authentication", "low"),
    "auth.token.failed": EventType("authentication", "medium"),
    "auth.token_exchange.succeeded": EventType("authentication", "low"),
    "auth.act_as.succeeded": EventType("authentication", "low"),
    "auth.act_as.refused": EventType("authentication", "medium"),
    "auth.password.changed": EventType("authentication", "medium"),
    "auth.password.reset": EventType("authentication", "medium"),
    # A password that verified but is breached or shorter than the policy now requires: the
    # person must change it before going further (auth/login_service.py; OWASP_PLAN.md OW-D17).
    "auth.password.weak": EventType("authentication", "medium"),
    "scim.auth.failed": EventType("authentication", "medium"),
    "embed.launch": EventType("authentication", "low"),
    "embed.launch.refused": EventType("authentication", "medium"),
    # access
    "access.denied": EventType("access", "medium"),
    "rate_limit.exceeded": EventType("access", "medium"),
    "sod.violation": EventType("access", "high"),  # critical when enforce refused it (from_audit_entry)
    "edge.secret.refused": EventType("access", "high"),
    # identity
    "user.created": EventType("identity", "medium"),
    "user.deactivated": EventType("identity", "medium"),
    "user.reactivated": EventType("identity", "medium"),
    "user.unlocked": EventType("identity", "medium"),
    "user.cross_tenant_access.granted": EventType("identity", "high"),
    "membership.changed": EventType("identity", "medium"),
    "user.invited": EventType("identity", "medium"),
    "user.email.change_requested": EventType("identity", "medium"),
    "user.email.changed": EventType("identity", "high"),  # OW-D31: the address someone signs in with
    "compliance.auto_accept.changed": EventType("configuration", "high"),  # attestations accepted without a person (CT4)
    "compliance.attestation.rejected": EventType("configuration", "medium"),
    "user.approved": EventType("identity", "medium"),  # the bank let in someone a customer added (MOBILE_PLAN.md R6)
    "user.rejected": EventType("identity", "medium"),
    "customer.self_service.changed": EventType("configuration", "high"),
    "security_group.created": EventType("identity", "medium"),
    "security_group.changed": EventType("identity", "high"),
    # integration
    "api_client.created": EventType("integration", "high"),
    "api_client.secret_rotated": EventType("integration", "medium"),
    "api_client.revoked": EventType("integration", "medium"),
    "sso_connection.changed": EventType("integration", "high"),
    "sso_connection.scim_token.issued": EventType("integration", "high"),
    "embed.origins.changed": EventType("integration", "medium"),
    "security_events.destination.changed": EventType("integration", "high"),
    "support_access.granted": EventType("identity", "high"),
    "support_access.approved": EventType("identity", "high"),
    "support_access.revoked": EventType("identity", "medium"),
    "support_access.used": EventType("authentication", "high"),
    "support_access.expired": EventType("identity", "low"),
    "tenant.provisioned": EventType("configuration", "high"),
    "webhook.changed": EventType("integration", "medium"),
    # configuration
    "settings.changed": EventType("configuration", "medium"),
    "sod.mode.changed": EventType("configuration", "high"),
    "plugin.enabled": EventType("configuration", "medium"),
    "plugin.disabled": EventType("configuration", "medium"),
    "change_request.created": EventType("configuration", "medium"),
    "change_request.approved": EventType("configuration", "medium"),
    "change_request.rejected": EventType("configuration", "low"),
    "change_request.withdrawn": EventType("configuration", "low"),
    # The service provider changed something for the whole installation (services/platform_log_service.py).
    "platform.action": EventType("configuration", "high"),
    "change_request.expired": EventType("configuration", "low"),
    # Nobody else could approve: applied after the cooling-off (DUAL_CONTROL_PLAN.md DC-D6).
    "change_request.applied_alone": EventType("configuration", "critical"),
    # Turning on a second-person switch refused: nobody else could approve (DC-D16).
    "change_request.switch_refused": EventType("configuration", "medium"),
    "ml_model.activated": EventType("configuration", "medium"),
    # data
    "audit.export": EventType("data", "medium"),
    "data_export.started": EventType("data", "high"),
    "data_export.downloaded": EventType("data", "high"),
    "bulk_upload.reversed": EventType("data", "medium"),
    "list.exported": EventType("data", "medium"),
    "users.exported": EventType("data", "medium"),
    # integrity
    "file.malware_detected": EventType("integrity", "high"),  # OW-D34: refused on upload, or quarantined later
    "anomaly_capture.failed": EventType("integrity", "high"),
    "secrets.reencrypted": EventType("integrity", "medium"),
    "control.status.changed": EventType("integrity", "high"),
    "audit.chain.verify_failed": EventType("integrity", "critical"),  # the provider's action log didn't verify
}

# Action-log actions that are also security events (event name, and the setting changed for settings.*).
AUDIT_EVENTS: dict[str, str] = {
    "user.create": "user.created",
    "user.deactivate": "user.deactivated",
    "user.reactivate": "user.reactivated",
    "user.remove_inactive": "user.deactivated",  # services/inactivity_service.py: access removed after the bank's days
    "user.unlock": "user.unlocked",
    "user.grant_cross_tenant_access": "user.cross_tenant_access.granted",
    "user.update_membership": "membership.changed",
    "user.account_access": "membership.changed",
    # A customer's own administrators (CUSTOMER_ADMIN_PLAN.md).
    "customer_people.invite": "user.invited",
    "customer_people.accept": "user.created",
    "customer_people.update": "membership.changed",
    "customer_people.deactivate": "user.deactivated",
    "customer_people.reactivate": "user.reactivated",
    "customer_people.unlock": "user.unlocked",
    "customer_people.reset_password": "auth.password.reset",
    "customer_people.sign_out_everywhere": "auth.session.revoked",
    "customer_people.export": "users.exported",
    "customer.self_service": "customer.self_service.changed",
    "access_review.removed": "user.deactivated",
    "compliance.auto_accept.changed": "compliance.auto_accept.changed",
    "compliance.attestation.rejected": "compliance.attestation.rejected",
    "customer_user.approve": "user.approved",
    "customer_user.reject": "user.rejected",
    "customer.approval": "customer.self_service.changed",
    "tenant.update_customer_approval": "customer.self_service.changed",
    "customer_people.revoke_invitation": "user.invited",
    "customer_sign_on.create": "sso_connection.changed",
    "customer_sign_on.update": "sso_connection.changed",
    "customer_sign_on.deactivate": "sso_connection.changed",
    "customer_sign_on.reactivate": "sso_connection.changed",
    "customer_sign_on.mapping": "sso_connection.changed",
    "customer_sign_on.mapping_removed": "sso_connection.changed",
    "customer_sign_on.domain_added": "sso_connection.changed",
    "customer_sign_on.domain_verified": "sso_connection.changed",
    "customer_sign_on.domain_removed": "sso_connection.changed",
    "sso.domain_added": "sso_connection.changed",
    "sso.domain_verified": "sso_connection.changed",
    "sso.domain_removed": "sso_connection.changed",
    "user.email.change_requested": "user.email.change_requested",
    "user.email.changed": "user.email.changed",
    "file.malware_detected": "file.malware_detected",
    "customer_sign_on.scim_token": "sso_connection.scim_token.issued",
    "customer_sign_on.scim_revoked": "sso_connection.changed",
    "customer_sign_on.password_sign_in": "settings.changed",
    "user.password_change": "auth.password.changed",
    "user.password_reset": "auth.password.reset",
    "user.password_reset_completed": "auth.password.changed",
    "user.security_key_added": "auth.mfa.added",
    "user.security_key_removed": "auth.mfa.removed",
    "user.security_key_reset": "auth.mfa.reset",
    "tenant.sessions.ended_all": "auth.session.revoked",
    "customer_people.sessions.ended_all": "auth.session.revoked",
    "user.session.ended": "auth.session.revoked",
    "customer_people.reset_security_keys": "auth.mfa.reset",
    "user.sign_out_everywhere": "auth.session.revoked",
    "user.sign_out_other_sessions": "auth.session.revoked",
    "user.login_sso": "auth.sso.succeeded",
    "user.login_embed": "auth.login.succeeded",
    "embed.launch": "embed.launch",
    "user.logout_sso": "auth.logout",
    "security_group.create": "security_group.created",
    "security_group.update": "security_group.changed",
    "api_client.create": "api_client.created",
    "api_client.activate": "api_client.created",  # a client made inactive, approved for use (DUAL_CONTROL_PLAN.md)
    "api_client.rotate_secret": "api_client.secret_rotated",
    "api_client.revoke": "api_client.revoked",
    "api_client.token_exchange": "auth.token_exchange.succeeded",
    "api_client.act_as": "auth.act_as.succeeded",
    "sso_connection.create": "sso_connection.changed",
    "sso_connection.update": "sso_connection.changed",
    "sso_connection.scim_token": "sso_connection.scim_token.issued",
    "sso_connection.mapping": "sso_connection.changed",
    "tenant.update_embedding": "embed.origins.changed",
    "tenant.update_security_event_destination": "security_events.destination.changed",
    "webhook.create": "webhook.changed",
    "support_access.grant": "support_access.granted",
    "support_access.approve": "support_access.approved",
    "support_access.revoke": "support_access.revoked",
    "support_access.use": "support_access.used",
    "support_access.expire": "support_access.expired",
    "tenant.provision": "tenant.provisioned",
    "webhook.update": "webhook.changed",
    "webhook.rotate_secret": "webhook.changed",
    "tenant.update_reminders": "settings.changed",
    "tenant.update_settings": "settings.changed",
    "tenant.update_session_timeout": "settings.changed",
    "tenant.update_dual_control": "settings.changed",
    "tenant.update_password_policy": "settings.changed",
    "tenant.update_access_rules": "settings.changed",
    "tenant.update_security_keys": "settings.changed",
    "tenant.update_api_key_pairs_only": "settings.changed",
    "customer.update_security_keys": "settings.changed",
    "tenant.update_approval_switches": "settings.changed",
    "customer.approval_switch": "settings.changed",
    "sso.password_login": "settings.changed",
    "tenant.update_data_export_timeout": "settings.changed",
    "tenant.update_decision_files": "settings.changed",
    "tenant.update_cutoffs": "settings.changed",  # when decisions become final (UI_PLAN.md UI-D13)
    "customer.update_cutoff": "settings.changed",
    "tenant.update_closures": "settings.changed",
    "customer.update_password_policy": "settings.changed",
    "tenant.update_sod_mode": "sod.mode.changed",
    "compliance.settings.changed": "settings.changed",  # the attestations' second person among them
    "tenant.ml_source_change": "settings.changed",  # pooling the bank's decisions into the shared model, or not
    "plugin.enable": "plugin.enabled",
    "plugin.disable": "plugin.disabled",
    "change_request.create": "change_request.created",
    "change_request.approve": "change_request.approved",
    "change_request.reject": "change_request.rejected",
    "change_request.withdraw": "change_request.withdrawn",
    "change_request.expire": "change_request.expired",
    "change_request.applied_alone": "change_request.applied_alone",
    "change_request.switch_refused": "change_request.switch_refused",
    "ml_model.activate": "ml_model.activated",
    "audit.export": "audit.export",
    "data_export.request": "data_export.started",
    "data_export.download": "data_export.downloaded",
    "list.export": "list.exported",
    "sod.violation": "sod.violation",
}


def _context():
    from openclear.web import request_context

    return request_context.current()


def emit(
    session: Session,
    name: str,
    *,
    tenant_id: uuid.UUID | None,
    outcome: str = "success",
    reason: str | None = None,
    actor_type: str | None = None,
    actor_id: Any = None,
    actor_label: str | None = None,
    target_type: str | None = None,
    target_id: Any = None,
    target_label: str | None = None,
    details: dict | None = None,
    audit_sequence: int | None = None,
    channel: str | None = None,
    severity: str | None = None,
) -> SecurityEvent:
    """Records the event in the caller's transaction (the caller commits) and logs it.
    The request's IP, user agent, ids and channel come from the request context. Who did it
    defaults to the request's signed-in person when not given. `severity` overrides the
    catalog's for this one event."""
    kind = CATALOG[name]
    context = _context()
    if actor_type is None and context is not None and (context.user_id or context.api_client_id):
        actor_type = "api_client" if context.api_client_id and not context.user_id else "user"
        actor_id = actor_id or context.user_id or context.api_client_id
        actor_label = actor_label or context.user_email
    event = SecurityEvent(
        tenant_id=tenant_id, name=name, category=kind.category, outcome=outcome, severity=severity or kind.severity,
        reason=(reason or None) and reason[:60],
        actor_type=actor_type, actor_id=str(actor_id) if actor_id else None, actor_label=(actor_label or None) and actor_label[:320],
        target_type=target_type, target_id=str(target_id) if target_id else None,
        target_label=(target_label or None) and target_label[:320],
        source_ip=context.client_ip if context else None, user_agent=context.user_agent if context else None,
        request_id=context.request_id if context else None, edge_id=context.edge_id if context else None,
        channel=channel or (context.channel if context else "system"), audit_sequence=audit_sequence, details=details or None,
    )
    session.add(event)
    session.flush()
    logger.info("security event %s", name, extra={"security_event": as_record(event)})
    return event


def from_audit_entry(session: Session, entry) -> SecurityEvent | None:
    """The security event for an action-log entry, if its action is one (AUDIT_EVENTS)."""
    metadata = entry.metadata_json or {}
    name = AUDIT_EVENTS.get(entry.action)
    if entry.action == "mrm.anomaly_capture" and metadata.get("passed") is False:
        name = "anomaly_capture.failed"
    if name is None:
        return None
    details = {"action": entry.action, "summary": entry.summary[:300]}
    if name == "settings.changed":
        details["setting"] = entry.action.split(".", 1)[1].removeprefix("update_")
    refused = name == "sod.violation" and bool(metadata.get("enforced"))
    outcome = "failure" if refused else "success"
    return emit(
        session, name, tenant_id=entry.tenant_id, outcome=outcome, reason=metadata.get("rule") if name == "sod.violation" else None,
        actor_type="user" if entry.actor_user_id else "system", actor_id=entry.actor_user_id,
        target_type=entry.resource_type, target_id=entry.resource_id, details=details, audit_sequence=entry.sequence,
        channel=entry.channel.value, severity="critical" if refused else None,  # a breach attempt enforce refused
    )


# --- Sampled events (floods become one event a minute per source, with a count) -------------

_samples: dict[tuple[str, str], tuple[float, int]] = {}
_samples_lock = threading.Lock()
SAMPLE_SECONDS = 60.0


def _sample(name: str, key: str) -> int | None:
    """None to skip this one; otherwise how many were skipped since the last one recorded."""
    now = time.monotonic()
    with _samples_lock:
        last, skipped = _samples.get((name, key), (0.0, 0))
        if now - last < SAMPLE_SECONDS:
            _samples[(name, key)] = (last, skipped + 1)
            return None
        _samples[(name, key)] = (now, 0)
        if len(_samples) > 10_000:  # bound memory under a flood from many sources
            cutoff = now - SAMPLE_SECONDS
            for k in [k for k, (t, _s) in _samples.items() if t < cutoff]:
                del _samples[k]
        return skipped


def emit_sampled(session: Session, name: str, *, key: str, tenant_id: uuid.UUID | None, **kwargs) -> SecurityEvent | None:
    skipped = _sample(name, key)
    if skipped is None:
        return None
    details = dict(kwargs.pop("details", None) or {})
    if skipped:
        details["since_last_event"] = skipped
    return emit(session, name, tenant_id=tenant_id, details=details, **kwargs)


def emit_detached(name: str, *, sampled_key: str | None = None, tenant_id: uuid.UUID | None = None, **kwargs) -> None:
    """For places with no database session of their own (middleware refusals): records the
    event in its own short transaction. Never raises; a failure is logged."""
    if sampled_key is not None and _sample(name, sampled_key) is None:
        return
    from openclear.db.session import get_session_factory

    try:
        session = (_detached_factory or get_session_factory())()
        try:
            emit(session, name, tenant_id=tenant_id, **kwargs)
            session.commit()
        finally:
            session.close()
    except Exception:  # noqa: BLE001 -- a refusal must never fail because its event couldn't be written
        logger.exception("Couldn't record security event %s", name)


_detached_factory = None  # the test suite points this at its own database


def use_session_factory(factory) -> None:
    global _detached_factory
    _detached_factory = factory


def reset_sampling_for_tests() -> None:
    with _samples_lock:
        _samples.clear()


# --- Reading -------------------------------------------------------------------------------------

def as_record(event: SecurityEvent, tenant_slug: str | None = None) -> dict:
    """The event in the published schema (SIEM_PLAN.md, "The event record")."""
    record: dict[str, Any] = {
        "id": f"evt_{event.id}" if event.id is not None else None,
        "sequence": event.id,
        "time": event.occurred_at.isoformat() if event.occurred_at else None,
        "event": {"name": event.name, "category": event.category, "outcome": event.outcome, "severity": event.severity,
                  **({"reason": event.reason} if event.reason else {})},
        "bank": {"id": str(event.tenant_id), **({"slug": tenant_slug} if tenant_slug else {})} if event.tenant_id else None,
        "actor": {"type": event.actor_type, "id": event.actor_id, **({"email": event.actor_label} if event.actor_label else {})}
        if event.actor_type else None,
        "target": {"type": event.target_type, "id": event.target_id, **({"name": event.target_label} if event.target_label else {})}
        if event.target_type else None,
        "source": {"ip": event.source_ip, **({"user_agent": event.user_agent} if event.user_agent else {})} if event.source_ip else None,
        "request": {"id": event.request_id, "channel": event.channel, **({"edge_id": event.edge_id} if event.edge_id else {})},
        "audit": {"sequence": event.audit_sequence} if event.audit_sequence else None,
        "details": event.details or None,
    }
    return {k: v for k, v in record.items() if v is not None}


def feed(session: Session, tenant_id: uuid.UUID | None, *, after: int = 0, limit: int = 100, all_banks: bool = False) -> list[SecurityEvent]:
    """Events after a cursor, oldest first: one bank's, or (all_banks, for the platform
    operator) every bank's and those belonging to none."""
    stmt = select(SecurityEvent).where(SecurityEvent.id > after)
    if not all_banks:
        stmt = stmt.where(SecurityEvent.tenant_id == tenant_id)
    return list(session.execute(stmt.order_by(SecurityEvent.id).limit(max(1, min(limit, 1000)))).scalars())
