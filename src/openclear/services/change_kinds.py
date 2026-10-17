# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The protected changes (DUAL_CONTROL_PLAN.md): for each kind, who may ask for it and approve
it, how to read the value now, when a change loosens (only loosening waits, DC-D2), and the code
that applies it, so an approved change does exactly what an immediate one would.

Tiers (DC-D3 to DC-D5):
- `protection` (tier A): turning a protection off or down. Always needs a second person.
- `sensitive` (tier B): when the bank's "Sensitive settings need a second admin" is on.
- `access`: widening a person's access, when "Access changes need a second admin" is on.

A kind's payload is what applying it needs; `current` reads the value it would replace, and
`value` the value it would set, both as JSON-able dicts, compared by `loosens`."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


class ChangeError(ValueError):
    """The message is fit to show."""


@dataclass(frozen=True)
class Kind:
    key: str
    title: str  # "Segregation of duties"
    tier: str  # protection, sensitive, access
    permission: str  # to ask for it and to approve it
    current: Callable[[Session, uuid.UUID | None, uuid.UUID | None, dict], dict]
    value: Callable[[dict], dict]  # the payload's new value, in current's shape
    loosens: Callable[[dict, dict], bool]
    apply: Callable[[Session, uuid.UUID | None, uuid.UUID | None, dict, uuid.UUID | None], str]  # returns what was done
    setting: Callable[[dict], str] = lambda payload: ""  # which setting, for one open request per setting
    describe: Callable[[dict, dict], str] | None = None  # (before, after) -> the request's summary
    scope: str = "bank"
    # The action-log entry the change makes when it's applied, as an immediate one would, so its
    # security event goes out on approval too.
    action: str | Callable[[dict], str] | None = None
    # Turning a second-person switch on: the permissions someone else must hold (DC-D16), by what turns on.
    switches: Callable[[Session, uuid.UUID | None, uuid.UUID | None, dict, dict], list[tuple[str, str]]] = field(
        default=lambda session, tenant_id, customer_id, before, after: [])


_KINDS: dict[str, Kind] = {}


def register(kind: Kind) -> Kind:
    _KINDS[kind.key] = kind
    return kind


def get(key: str) -> Kind:
    try:
        return _KINDS[key]
    except KeyError:
        raise ChangeError(f"Unknown change {key!r}") from None


def all_kinds() -> list[Kind]:
    return list(_KINDS.values())


# --- Helpers for changes that set a few of the bank's own columns -------------------------------


def tenant_fields(*names: str) -> Callable:
    def read(session: Session, tenant_id, customer_id, payload: dict) -> dict:
        from openclear.domain.tenant import Tenant

        tenant = session.get(Tenant, tenant_id)
        return {n: getattr(tenant, n) for n in names}
    return read


def payload_values(payload: dict) -> dict:
    return dict(payload.get("values") or {})


def turned_off(*names: str) -> Callable[[dict, dict], bool]:
    """Loosens when any of these goes from on to off."""
    return lambda before, after: any(before.get(n) and not after.get(n, before.get(n)) for n in names)


def turned_on(*names: str) -> Callable[[dict, dict], bool]:
    return lambda before, after: any(not before.get(n) and after.get(n, before.get(n)) for n in names)


# --- The existing two (COMPLIANCE_PLAN.md C1), now behind the bank's "sensitive" switch ---------


def _rule_current(session, tenant_id, customer_id, payload) -> dict:
    from openclear.services import plugin_service

    return {"enabled": plugin_service.is_enabled(session, tenant_id, "matching_rules", payload["name"])}


def _rule_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services import plugin_service

    plugin_service.set_enabled(session, tenant_id, "matching_rules", payload["name"], bool(payload["enabled"]), user_id=user_id,
                               config=payload.get("config") or None)
    return f"Matching rule {payload['name']} turned {'on' if payload['enabled'] else 'off'}"


register(Kind(
    "matching_rule.set", "Matching rules", "sensitive", "admin:manage", _rule_current,
    value=lambda payload: {"enabled": bool(payload["enabled"])},
    loosens=lambda before, after: True,  # on or off, it changes how items are judged
    apply=_rule_apply, setting=lambda payload: f"matching_rule:{payload['name']}",
    action=lambda payload: "plugin.enable" if payload.get("enabled") else "plugin.disable",
))


def _model_current(session, tenant_id, customer_id, payload) -> dict:
    from openclear.domain.ml_model import MlModel

    model = session.get(MlModel, uuid.UUID(payload["model_id"]))
    from openclear.domain.ml_model import MlModelStatus

    return {"active": bool(model and model.status == MlModelStatus.ACTIVE)}


def _model_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.ml.registry import activate_model
    from openclear.services import customer_service

    model_customer = uuid.UUID(payload["customer_id"]) if payload.get("customer_id") else None
    if model_customer is not None and customer_service.get_customer(session, tenant_id, model_customer) is None:
        raise ChangeError("That customer no longer exists.")
    model = activate_model(session, uuid.UUID(payload["model_id"]), expected_customer_id=model_customer,
                           **({} if model_customer else {"expected_tenant_id": tenant_id}))
    return f"Activated {model.network_code} model {model.version}"


register(Kind(
    "ml_model.activate", "Scoring models", "sensitive", "admin:manage", _model_current,
    value=lambda payload: {"active": True}, loosens=lambda before, after: True, apply=_model_apply,
    setting=lambda payload: f"ml_model:{payload.get('customer_id') or 'bank'}",
    action="ml_model.activate",
))


# --- Tier A: the bank's protections, off or down (DC-D3) ----------------------------------------

_BANK_ADMIN = "tenant:manage"


def _set_tenant(**names) -> Callable:
    def apply(session, tenant_id, customer_id, payload, user_id) -> str:
        from openclear.domain.tenant import Tenant

        tenant = session.get(Tenant, tenant_id)
        for name, value in payload_values(payload).items():
            setattr(tenant, name, value)
        session.flush()
        return ""
    return apply


def _dual_control_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services.tenant_service import set_require_dual_control

    on = bool(payload_values(payload)["require_dual_control"])
    set_require_dual_control(session, tenant_id, on)
    return f"Dual control on decisions {'on' if on else 'off'}"


register(Kind(
    "dual_control", "Dual control on decisions", "protection", _BANK_ADMIN, tenant_fields("require_dual_control"), payload_values,
    loosens=turned_off("require_dual_control"), apply=_dual_control_apply,
    describe=lambda before, after: f"Turn {'on' if after['require_dual_control'] else 'off'} dual control on decisions",
    switches=lambda s, t, c, before, after: [("pay and return decisions", "exception:decide")] if turned_on("require_dual_control")(before, after) else [],
    action="tenant.update_dual_control",
))


def _sod_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.auth import sod

    mode = payload_values(payload)["sod_mode"]
    sod.set_mode(session, tenant_id, mode)  # raises ValueError for enforce with conflicting groups
    return f"Segregation of duties set to {mode}"


register(Kind(
    "sod_mode", "Segregation of duties", "protection", _BANK_ADMIN, tenant_fields("sod_mode"), payload_values,
    loosens=lambda before, after: before["sod_mode"] == "enforce" and after["sod_mode"] != "enforce", apply=_sod_apply,
    describe=lambda before, after: f"Set segregation of duties to {after['sod_mode']}",
    action="tenant.update_sod_mode",
))


def _switch_functions(session, tenant_id, customer_id, before, after) -> list[tuple[str, str]]:
    """Turning on a second-admin switch needs someone else for each function it covers (DC-D16)."""
    out: list[tuple[str, str]] = []
    if turned_on("sensitive_changes_need_approval")(before, after):
        seen = set()
        for kind in all_kinds():
            if kind.tier == "sensitive" and kind.scope == "bank" and kind.permission not in seen:
                seen.add(kind.permission)
                out.append((kind.title.lower(), kind.permission))
    if turned_on("access_changes_need_approval")(before, after):
        out.append(("people's access", "user:manage"))
    return out


register(Kind(
    "approval_switches", "Second admin for changes", "protection", _BANK_ADMIN,
    tenant_fields("sensitive_changes_need_approval", "access_changes_need_approval"), payload_values,
    loosens=turned_off("sensitive_changes_need_approval", "access_changes_need_approval"), apply=_set_tenant(),
    describe=lambda before, after: "Second admin: " + "; ".join(
        f"{label} {'on' if after[name] else 'off'}" for name, label in (("sensitive_changes_need_approval", "sensitive settings"),
                                                                        ("access_changes_need_approval", "access changes"))
        if after[name] != before[name]),
    switches=_switch_functions,
    action="tenant.update_approval_switches",
))

_ACCESS_RULES = ("login_max_failed_attempts", "login_lockout_minutes", "lockout_until_unlocked", "deactivate_inactive",
                 "deactivate_inactive_days", "remove_inactive", "remove_inactive_days")


def _access_rules_loosen(before: dict, after: dict) -> bool:
    from openclear.config import get_settings

    settings = get_settings()

    def attempts(v):
        return v["login_max_failed_attempts"] or settings.login_max_failed_attempts

    def minutes(v):
        return v["login_lockout_minutes"] or settings.login_lockout_minutes

    return (attempts(after) > attempts(before) or minutes(after) < minutes(before)
            or (before["lockout_until_unlocked"] and not after["lockout_until_unlocked"])
            or (before["deactivate_inactive"] and not after["deactivate_inactive"])
            or (after["deactivate_inactive"] and before["deactivate_inactive"] and after["deactivate_inactive_days"] > before["deactivate_inactive_days"])
            or (before["remove_inactive"] and not after["remove_inactive"])
            or (after["remove_inactive"] and before["remove_inactive"] and after["remove_inactive_days"] > before["remove_inactive_days"]))


def _access_rules_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services.tenant_service import InvalidTenantSettingsInput, set_access_rules

    v = payload_values(payload)
    try:
        set_access_rules(session, tenant_id, max_failed_attempts=v["login_max_failed_attempts"], lockout_minutes=v["login_lockout_minutes"],
                         lockout_until_unlocked=v["lockout_until_unlocked"], deactivate_inactive=v["deactivate_inactive"],
                         deactivate_inactive_days=v["deactivate_inactive_days"], remove_inactive=v["remove_inactive"],
                         remove_inactive_days=v["remove_inactive_days"])
    except InvalidTenantSettingsInput as exc:
        raise ChangeError(str(exc)) from None
    return "Lockout and inactive account rules changed"


register(Kind(
    "access_rules", "Lockout and inactive accounts", "protection", _BANK_ADMIN, tenant_fields(*_ACCESS_RULES), payload_values,
    loosens=_access_rules_loosen, apply=_access_rules_apply, describe=lambda before, after: "Loosen lockout or inactive account rules",
    action="tenant.update_access_rules",
))

_PASSWORD = ("password_min_length", "password_require_uppercase", "password_require_lowercase", "password_require_number",
             "password_require_symbol")


def _password_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services.tenant_service import InvalidTenantSettingsInput, set_password_policy

    v = payload_values(payload)
    try:
        set_password_policy(session, tenant_id, min_length=v["password_min_length"], require_uppercase=v["password_require_uppercase"],
                            require_lowercase=v["password_require_lowercase"], require_number=v["password_require_number"],
                            require_symbol=v["password_require_symbol"])
    except InvalidTenantSettingsInput as exc:
        raise ChangeError(str(exc)) from None
    return f"Password policy changed (minimum length {v['password_min_length']})"


register(Kind(
    "password_policy", "Password policy", "protection", _BANK_ADMIN, tenant_fields(*_PASSWORD), payload_values,
    loosens=lambda before, after: after["password_min_length"] < before["password_min_length"] or turned_off(*_PASSWORD[1:])(before, after),
    apply=_password_apply, describe=lambda before, after: "Weaken the password policy",
    action="tenant.update_password_policy",
))

register(Kind(
    "attest_second_person", "Second person on attestations", "protection", "compliance:manage", tenant_fields("attest_second_person"),
    payload_values, loosens=turned_off("attest_second_person"), apply=_set_tenant(),
    describe=lambda before, after: f"Turn {'on' if after['attest_second_person'] else 'off'} the second person on attestations",
    switches=lambda s, t, c, before, after: [("CUEC attestations", "compliance:attest")] if turned_on("attest_second_person")(before, after) else [],
    action="compliance.settings.changed",
))

register(Kind(
    "customer_approval", "Approving new customer users", "protection", _BANK_ADMIN, tenant_fields("approve_customer_users"), payload_values,
    loosens=turned_off("approve_customer_users"), apply=_set_tenant(),
    describe=lambda before, after: f"New customer users {'wait' if after['approve_customer_users'] else 'no longer wait'} for the bank",
    action="tenant.update_customer_approval",
))

def _password_login_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services import sso_service

    enabled = bool(payload_values(payload)["password_login_enabled"])
    try:
        sso_service.set_tenant_password_login_enabled(session, tenant_id, enabled)
    except ValueError as exc:
        raise ChangeError(str(exc)) from None
    return "Staff can sign in with passwords" if enabled else "Staff must sign in with single sign-on"


register(Kind(
    "password_login", "Password sign-in", "protection", _BANK_ADMIN, tenant_fields("password_login_enabled"), payload_values,
    loosens=turned_on("password_login_enabled"), apply=_password_login_apply,
    describe=lambda before, after: "Let staff sign in with passwords again" if after["password_login_enabled"] else "Require single sign-on",
    action="sso.password_login",
))


def _siem_current(session, tenant_id, customer_id, payload) -> dict:
    from openclear.services.siem import delivery

    destination = delivery.bank_destination(session, tenant_id)
    return {"destination": delivery.describe(destination) if destination else None, "enabled": bool(destination and destination.enabled)}


def _siem_value(payload: dict) -> dict:
    if payload.get("remove"):
        return {"destination": None, "enabled": False}
    return {"destination": payload.get("describe"), "enabled": bool(payload.get("enabled"))}


def _siem_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.auth.crypto import decrypt_secret
    from openclear.services.siem import delivery

    if payload.get("remove"):
        delivery.remove_bank_destination(session, tenant_id)
        return "Stopped forwarding security events"
    fields = dict(payload["fields"])
    if payload.get("token_encrypted"):
        fields["token"] = decrypt_secret(payload["token_encrypted"])
    try:
        destination = delivery.save_bank_destination(session, tenant_id, **fields)
    except delivery.DestinationError as exc:
        raise ChangeError(str(exc)) from None
    return f"Security events forwarded to {delivery.describe(destination)}"


register(Kind(
    "security_event_forwarding", "Security event forwarding", "protection", _BANK_ADMIN, _siem_current, _siem_value,
    loosens=lambda before, after: True,  # stopping it, pausing it, or sending the bank's events somewhere new
    apply=_siem_apply,
    describe=lambda before, after: "Stop forwarding security events" if not after["destination"]
    else f"Forward security events to {after['destination']}" + ("" if after["enabled"] else " (paused)"),
    action="tenant.update_security_event_destination",
))


# --- Tier B: sensitive settings, when the bank's switch is on (DC-D4) -----------------------------


def _group_current(session, tenant_id, customer_id, payload) -> dict:
    from openclear.services import security_group_service

    group = security_group_service.get_security_group(session, tenant_id, uuid.UUID(payload["group_id"])) if payload.get("group_id") else None
    if payload.get("group_id") and group is None:
        raise ChangeError("That security group no longer exists.")
    return {"name": group.name if group else None, "permissions": sorted(group.permissions or []) if group else [],
            "offered_to_customers": bool(group and group.offered_to_customers)}


def _group_value(payload: dict) -> dict:
    from openclear.services.security_group_service import _clean_permissions

    return {"name": payload["name"], "permissions": sorted(_clean_permissions(payload.get("permissions") or [])),
            "offered_to_customers": bool(payload.get("offered_to_customers"))}


def _group_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from sqlalchemy.exc import IntegrityError

    from openclear.services import security_group_service

    data = security_group_service.SecurityGroupInput(name=payload["name"], permissions=payload.get("permissions") or [],
                                                     offered_to_customers=bool(payload.get("offered_to_customers")))
    try:
        with session.begin_nested():
            if payload.get("group_id"):
                group = security_group_service.update_security_group(session, tenant_id, uuid.UUID(payload["group_id"]), data)
                if group is None:
                    raise ChangeError("That security group no longer exists.")
                return f"Updated security group {group.name} ({len(group.permissions)} permissions)"
            group = security_group_service.create_security_group(session, tenant_id, data)
            return f"Created security group {group.name} ({len(group.permissions)} permissions)"
    except IntegrityError:
        raise ChangeError("A security group with that name already exists.") from None


register(Kind(
    "security_group.save", "Security groups", "sensitive", "security_group:manage", _group_current, _group_value,
    loosens=lambda before, after: bool(set(after["permissions"]) - set(before["permissions"]))
    or (after["offered_to_customers"] and not before["offered_to_customers"]),
    apply=_group_apply, setting=lambda payload: f"security_group:{payload.get('group_id') or 'new:' + payload['name'].strip().lower()}",
    describe=lambda before, after: (f"Give the security group {after['name']} "
                                    + ", ".join(sorted(set(after["permissions"]) - set(before["permissions"])) or ["no new permissions"])
                                    + (" and offer it to customers" if after["offered_to_customers"] and not before["offered_to_customers"] else ""))
    if before["name"] else f"Create the security group {after['name']} with {', '.join(after['permissions']) or 'no permissions'}",
    action=lambda payload: "security_group.update" if payload.get("group_id") else "security_group.create",
))


def _client_parts(session, tenant_id, payload):
    from openclear.domain.api_client import ApiClient
    from openclear.domain.tenant_membership import TenantMembership
    from openclear.domain.user import User

    client = session.get(ApiClient, uuid.UUID(payload["api_client_id"]))
    if client is None or client.tenant_id != tenant_id:
        raise ChangeError("That API client no longer exists.")
    return client, session.get(TenantMembership, client.membership_id), session.get(User, client.user_id)


def _client_current(session, tenant_id, customer_id, payload) -> dict:
    client, membership, user = _client_parts(session, tenant_id, payload)
    return {"active": bool(client.revoked_at is None and membership.is_active and user.is_active)}


def _client_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    client, membership, user = _client_parts(session, tenant_id, payload)
    if client.revoked_at is not None:
        raise ChangeError("That API client was revoked.")
    membership.is_active, user.is_active = True, True
    session.flush()
    return f"API client {client.name} ({client.client_id}) can now be used"


register(Kind(
    "api_client.activate", "API clients", "sensitive", "api_client:manage", _client_current, lambda payload: {"active": True},
    loosens=lambda before, after: after["active"] and not before["active"], apply=_client_apply,
    setting=lambda payload: f"api_client:{payload['api_client_id']}", action="api_client.activate",
))


def _webhook_current(session, tenant_id, customer_id, payload) -> dict:
    from openclear.services import webhook_service

    endpoint = webhook_service.get_endpoint(session, tenant_id, uuid.UUID(payload["endpoint_id"]))
    if endpoint is None:
        raise ChangeError("That webhook no longer exists.")
    return {"url": endpoint.url, "event_types": sorted(endpoint.event_types or []), "description": endpoint.description or "",
            "is_active": bool(endpoint.is_active)}


def _webhook_value(payload: dict) -> dict:
    return {"url": payload["url"].strip(), "event_types": sorted(payload.get("event_types") or []),
            "description": (payload.get("description") or "").strip()[:200], "is_active": bool(payload["is_active"])}


def _webhook_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services import webhook_service

    try:
        endpoint = webhook_service.update_endpoint(session, tenant_id, uuid.UUID(payload["endpoint_id"]), url=payload["url"],
                                                   event_types=payload.get("event_types") or [], description=payload.get("description"),
                                                   is_active=bool(payload["is_active"]))
    except webhook_service.WebhookError as exc:
        raise ChangeError(str(exc)) from None
    return f"Webhook {endpoint.url} {'on' if endpoint.is_active else 'off'}"


register(Kind(
    "webhook.save", "Webhooks", "sensitive", "api_client:manage", _webhook_current, _webhook_value,
    # Somewhere new, more events, or on again: the bank's data goes further. Off, or fewer events, never waits.
    loosens=lambda before, after: after["is_active"] and (not before["is_active"] or after["url"] != before["url"]
                                                          or bool(set(after["event_types"]) - set(before["event_types"]))),
    apply=_webhook_apply, setting=lambda payload: f"webhook:{payload['endpoint_id']}",
    describe=lambda before, after: f"Send webhooks to {after['url']}" + ("" if before["is_active"] else " (a new endpoint)"),
    action="webhook.update",
))


def _outbox_current(session, tenant_id, customer_id, payload) -> dict:
    from openclear.services import decision_outbox_service

    outbox = decision_outbox_service.get_outbox(session, tenant_id)
    return {"enabled": bool(outbox.enabled), "layout": outbox.layout, "interval_minutes": outbox.interval_minutes,
            "settings": (outbox.layout_settings or {}).get(outbox.layout, {})}


def _outbox_value(payload: dict) -> dict:
    return {"enabled": bool(payload["enabled"]), "layout": payload["layout"], "interval_minutes": int(payload["interval_minutes"]),
            "settings": dict(payload.get("settings") or {})}


def _outbox_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services import decision_outbox_service

    try:
        decision_outbox_service.save(session, tenant_id, enabled=bool(payload["enabled"]), layout=payload["layout"],
                                     interval_minutes=int(payload["interval_minutes"]), settings=dict(payload.get("settings") or {}))
    except decision_outbox_service.OutboxError as exc:
        raise ChangeError(str(exc)) from None
    return f"Decision files {'on' if payload['enabled'] else 'off'}: {payload['layout']}, every {payload['interval_minutes']} minutes"


register(Kind(
    "decision_files", "Decision files", "sensitive", _BANK_ADMIN, _outbox_current, _outbox_value,
    # On, or sent another way or somewhere else: the bank's decisions go somewhere new. Off, or only how often, never waits.
    loosens=lambda before, after: after["enabled"] and (not before["enabled"] or after["layout"] != before["layout"]
                                                        or after["settings"] != before["settings"]),
    apply=_outbox_apply, describe=lambda before, after: f"Write decision files as {after['layout']}" + ("" if before["enabled"] else " (turning them on)"),
    action="tenant.update_decision_files",
))


def _origins_current(session, tenant_id, customer_id, payload) -> dict:
    from openclear.services import embed_service

    return {"origins": sorted(embed_service.allowed_origins(session, tenant_id))}


def _origins_value(payload: dict) -> dict:
    from openclear.services import embed_service

    try:
        return {"origins": sorted(embed_service.parse_origins(payload["text"]))}
    except embed_service.EmbedError as exc:
        raise ChangeError(str(exc)) from None


def _origins_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services import embed_service

    try:
        origins = embed_service.set_allowed_origins(session, tenant_id, payload["text"])
    except embed_service.EmbedError as exc:
        raise ChangeError(str(exc)) from None
    return f"Embedding origins: {', '.join(origins) or 'none (embedding off)'}"


register(Kind(
    "embedding", "Embedding in online banking", "sensitive", _BANK_ADMIN, _origins_current, _origins_value,
    loosens=lambda before, after: bool(set(after["origins"]) - set(before["origins"])), apply=_origins_apply,
    describe=lambda before, after: "Let " + ", ".join(sorted(set(after["origins"]) - set(before["origins"]))) + " frame OpenClear",
    action="tenant.update_embedding",
))


# Single sign-on: who can sign in, and as what. Bank connections here; a customer's in DC3.

def _connection(session, tenant_id, customer_id, payload):
    from openclear.services import sso_service

    connection = sso_service.get_connection(session, tenant_id, uuid.UUID(payload["connection_id"]))
    if connection is None or connection.customer_id != customer_id:
        raise ChangeError("That sign-on connection no longer exists.")
    return connection


def _connection_activate(session, tenant_id, customer_id, payload, user_id) -> str:
    connection = _connection(session, tenant_id, customer_id, payload)
    connection.is_active = True
    session.flush()
    return f"Sign-on connection {connection.display_name} on"


register(Kind(
    "sso_connection.activate", "Single sign-on", "sensitive", _BANK_ADMIN,
    lambda s, t, c, p: {"active": bool(_connection(s, t, c, p).is_active)}, lambda payload: {"active": True},
    loosens=lambda before, after: after["active"] and not before["active"], apply=_connection_activate,
    setting=lambda payload: f"sso:{payload['connection_id']}:active", action="sso_connection.update",
))

_SSO_FIELDS = ("provider", "issuer", "client_id", "groups_claim_name", "auto_provision", "protocol", "saml_sso_url", "saml_slo_url",
               "saml_idp_certificate", "saml_email_attribute", "allow_idp_initiated", "customer_claim")


def _sso_public(values: dict) -> dict:
    out = {k: (v.value if hasattr(v, "value") else v) for k, v in values.items() if k in _SSO_FIELDS}
    out = {k: (v.strip() if isinstance(v, str) else v) or (False if isinstance(v, bool) else None) for k, v in out.items()}
    out["groups_claim_name"] = out.get("groups_claim_name") or "groups"
    if out.get("saml_idp_certificate"):
        import hashlib

        out["saml_idp_certificate"] = hashlib.sha256(out["saml_idp_certificate"].encode()).hexdigest()[:16]
    return out


def _sso_current(session, tenant_id, customer_id, payload) -> dict:
    connection = _connection(session, tenant_id, customer_id, payload)
    return {**_sso_public({k: getattr(connection, k, None) for k in _SSO_FIELDS}), "display_name": connection.display_name, "secret": "kept"}


def _sso_value(payload: dict) -> dict:
    data = payload["input"]
    return {**_sso_public(data), "display_name": data["display_name"], "secret": "new" if payload.get("client_secret_encrypted") else "kept"}


def _sso_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.auth.crypto import decrypt_secret
    from openclear.auth.outbound_http import UnsafeUrlError
    from openclear.domain.sso_connection import SsoProtocol, SsoProvider
    from openclear.services import sso_service

    data = dict(payload["input"])
    data["provider"], data["protocol"] = SsoProvider(data["provider"]), SsoProtocol(data["protocol"])
    data["customer_id"] = uuid.UUID(data["customer_id"]) if data.get("customer_id") else None
    data["client_secret"] = decrypt_secret(payload["client_secret_encrypted"]) if payload.get("client_secret_encrypted") else None
    try:
        connection = sso_service.update_connection(session, tenant_id, uuid.UUID(payload["connection_id"]), sso_service.SsoConnectionInput(**data))
    except (UnsafeUrlError, ValueError) as exc:
        raise ChangeError(str(exc)) from None
    return f"Updated sign-on connection {connection.display_name}"


register(Kind(
    "sso_connection.update", "Single sign-on", "sensitive", _BANK_ADMIN, _sso_current, _sso_value,
    # Only renaming it is harmless; anything else changes who can sign in, or how.
    loosens=lambda before, after: {k: v for k, v in before.items() if k != "display_name"} != {k: v for k, v in after.items() if k != "display_name"},
    apply=_sso_apply, setting=lambda payload: f"sso:{payload['connection_id']}:config",
    describe=lambda before, after: f"Change how {after['display_name']} signs people in", action="sso_connection.update",
))


def _mapping_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services import sso_service

    connection = _connection(session, tenant_id, customer_id, payload)
    try:
        sso_service.add_group_mapping(session, tenant_id, connection.id, external_group=payload["external_group"],
                                      security_group_id=uuid.UUID(payload["security_group_id"]), priority=int(payload.get("priority") or 100))
    except ValueError as exc:
        raise ChangeError(str(exc)) from None
    return f"{connection.display_name}: people in {payload['external_group']} get a group"


def _mapping_current(session, tenant_id, customer_id, payload) -> dict:
    from openclear.services import sso_service

    connection = _connection(session, tenant_id, customer_id, payload)
    existing = {(m.external_group, str(m.security_group_id)) for m in sso_service.list_group_mappings(session, tenant_id, connection.id)}
    return {"mapped": (payload["external_group"], payload["security_group_id"]) in existing}


register(Kind(
    "sso_mapping.add", "Single sign-on", "sensitive", _BANK_ADMIN, _mapping_current, lambda payload: {"mapped": True},
    loosens=lambda before, after: after["mapped"] and not before["mapped"], apply=_mapping_apply,
    setting=lambda payload: f"sso:{payload['connection_id']}:mapping:{payload['external_group']}",
    describe=lambda before, after: "Give people from the identity provider a security group", action="sso_connection.mapping",
))


def _scim_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from datetime import datetime, timezone

    connection = _connection(session, tenant_id, customer_id, payload)
    connection.scim_token_hash, connection.scim_token_created_at = payload["token_hash"], datetime.now(timezone.utc)
    session.flush()
    return f"SCIM token for {connection.display_name} works"


register(Kind(
    "scim_token.set", "Single sign-on", "sensitive", _BANK_ADMIN,
    lambda s, t, c, p: {"token": _connection(s, t, c, p).scim_token_hash or None}, lambda payload: {"token": payload["token_hash"]},
    loosens=lambda before, after: after["token"] != before["token"], apply=_scim_apply,
    setting=lambda payload: f"sso:{payload['connection_id']}:scim",
    describe=lambda before, after: "Let a directory create and change people over SCIM", action="sso_connection.scim_token",
))


def _self_service_current(session, tenant_id, customer_id, payload) -> dict:
    from openclear.services import customer_service

    customer = customer_service.get_customer(session, tenant_id, uuid.UUID(payload["customer_id"]))
    if customer is None:
        raise ChangeError("That customer no longer exists.")
    return {"users": bool(customer.self_manages_users), "sso": bool(customer.self_manages_sso), "api_clients": bool(customer.self_manages_api_clients)}


def _self_service_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services import customer_people_service

    try:
        customer = customer_people_service.set_self_service(session, tenant_id, uuid.UUID(payload["customer_id"]), users=bool(payload["users"]),
                                                            sso=bool(payload["sso"]), api_clients=bool(payload["api_clients"]))
    except customer_people_service.PeopleError as exc:
        raise ChangeError(str(exc)) from None
    return (f"{customer.name} manages its own people: {'yes' if payload['users'] else 'no'}; its own sign-on: "
            f"{'yes' if payload['sso'] else 'no'}; its own API clients: {'yes' if payload['api_clients'] else 'no'}")


register(Kind(
    "customer.self_service", "Customer self-service", "sensitive", "customer:manage", _self_service_current,
    lambda payload: {"users": bool(payload["users"]), "sso": bool(payload["sso"]), "api_clients": bool(payload["api_clients"])},
    loosens=turned_on("users", "sso", "api_clients"), apply=_self_service_apply,
    setting=lambda payload: f"self_service:{payload['customer_id']}", action="customer.self_service",
))


def _ml_source_current(session, tenant_id, customer_id, payload) -> dict:
    from openclear.domain.tenant import Tenant

    source = session.get(Tenant, tenant_id).ml_model_source
    return {"source": source.value if hasattr(source, "value") else source}


def _ml_shared_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services import tenant_ml_service

    try:
        tenant_ml_service.switch_to_shared(session, tenant_id, actor_user_id=uuid.UUID(payload["consented_by"]), consented=True)
    except tenant_ml_service.SwitchNotAllowed as exc:
        raise ChangeError(str(exc)) from None
    return "Switched to the shared fraud-scoring model (data pooling acknowledged)"


register(Kind(
    "ml.switch_to_shared", "The shared scoring model", "sensitive", _BANK_ADMIN, _ml_source_current, lambda payload: {"source": "shared"},
    loosens=lambda before, after: after["source"] == "shared" and before["source"] != "shared", apply=_ml_shared_apply,
    describe=lambda before, after: "Pool the bank's decisions into the shared fraud-scoring model", action="tenant.ml_source_change",
))


# --- Access: widening a person's access, when "Access changes need a second admin" is on (DC-D5) ---


def _membership(session, tenant_id, payload):
    from openclear.domain.tenant_membership import TenantMembership

    membership = session.get(TenantMembership, uuid.UUID(payload["membership_id"]))
    if membership is None or membership.tenant_id != tenant_id:
        raise ChangeError("That person's access no longer exists.")
    return membership


def _membership_on(session, tenant_id, customer_id, payload, user_id) -> str:
    membership = _membership(session, tenant_id, payload)
    membership.is_active, membership.pending_approval_at = True, None
    from datetime import datetime, timezone

    membership.last_active_at, membership.inactivity_warned_at = datetime.now(timezone.utc), None  # a fresh start for the inactivity rules
    session.flush()
    return f"Access for {_member_email(session, membership)} on"


def _member_email(session, membership) -> str:
    from openclear.domain.user import User

    user = session.get(User, membership.user_id)
    return user.email if user else str(membership.user_id)


register(Kind(
    "access.membership", "People's access", "access", "user:manage",
    lambda s, t, c, p: {"active": bool(_membership(s, t, p).is_active and _membership(s, t, p).pending_approval_at is None)},
    lambda payload: {"active": True}, loosens=lambda before, after: after["active"] and not before["active"], apply=_membership_on,
    setting=lambda payload: f"membership:{payload['membership_id']}:active", action="user.reactivate",
))


def _group_permissions(session, tenant_id, group_id) -> set[str]:
    from openclear.services import security_group_service

    group = security_group_service.get_security_group(session, tenant_id, group_id) if group_id else None
    return set(group.permissions or []) if group else set()


def _entitlements_current(session, tenant_id, customer_id, payload) -> dict:
    membership = _membership(session, tenant_id, payload)
    return {"security_group_id": str(membership.security_group_id), "customer_id": str(membership.customer_id) if membership.customer_id else None,
            "permissions": sorted(_group_permissions(session, tenant_id, membership.security_group_id))}


def _entitlements_loosen(before: dict, after: dict) -> bool:
    """More permissions, or a wider or different customer scope (bank-wide is widest)."""
    return bool(set(after["permissions"]) - set(before["permissions"])) or (after["customer_id"] != before["customer_id"]
                                                                              and before["customer_id"] is not None)


def _entitlements_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services import user_service

    membership = _membership(session, tenant_id, payload)
    try:
        user_service.update_membership(session, tenant_id, membership.id, security_group_id=uuid.UUID(payload["security_group_id"]),
                                       customer_id=uuid.UUID(payload["customer_id"]) if payload.get("customer_id") else None)
    except ValueError as exc:
        raise ChangeError(str(exc)) from None
    return f"Changed what {_member_email(session, membership)} may do"


register(Kind(
    "access.entitlements", "People's access", "access", "user:manage", _entitlements_current,
    # The new group's permissions come with the payload, read when it was asked for.
    lambda payload: {"security_group_id": payload["security_group_id"], "customer_id": payload.get("customer_id") or None,
                     "permissions": sorted(payload.get("permissions") or [])},
    loosens=_entitlements_loosen, apply=_entitlements_apply,
    setting=lambda payload: f"membership:{payload['membership_id']}:entitlements", action="user.update_membership",
))


def _accounts_current(session, tenant_id, customer_id, payload) -> dict:
    from openclear.services import account_access_service

    access = account_access_service.get_access(session, tenant_id, uuid.UUID(payload["membership_id"]))
    return {"all_accounts": bool(access.all_accounts), "account_ids": sorted(str(a) for a in access.account_ids)}


def _accounts_apply(session, tenant_id, customer_id, payload, user_id) -> str:
    from openclear.services import account_access_service

    try:
        account_access_service.set_access(session, tenant_id, uuid.UUID(payload["membership_id"]), all_accounts=bool(payload["all_accounts"]),
                                          account_ids=[uuid.UUID(a) for a in payload.get("account_ids") or []])
    except ValueError as exc:
        raise ChangeError(str(exc)) from None
    return "Changed which accounts a person sees"


register(Kind(
    "access.accounts", "People's access", "access", "user:manage", _accounts_current,
    lambda payload: {"all_accounts": bool(payload["all_accounts"]), "account_ids": sorted(payload.get("account_ids") or [])},
    loosens=lambda before, after: (after["all_accounts"] and not before["all_accounts"])
    or (not after["all_accounts"] and not before["all_accounts"] and bool(set(after["account_ids"]) - set(before["account_ids"]))),
    apply=_accounts_apply, setting=lambda payload: f"membership:{payload['membership_id']}:accounts", action="user.account_access",
))
