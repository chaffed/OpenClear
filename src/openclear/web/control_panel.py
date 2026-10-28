# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The control panel (UX_PLAN.md U11): Settings and Admin as one page of tiles, grouped
into sections, each with its current state. Small settings open in a modal and have their
own URL (/ui/settings/<key>, a plain page without JavaScript); lists and workflows open
as pages. A tile appears only when the person's permissions allow what it opens.

A bank's staff see the bank's tiles; a customer's own users see their customer's."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.db.tenancy import TenantContext

SECTIONS = ("Organization", "Security & access", "Decisions & rules", "Integrations", "Data & compliance")

# Who sees the panel at all: anyone holding a permission one of its tiles needs.
BANK_PERMISSIONS = ("tenant:manage", "admin:manage", "user:manage", "security_group:manage", "api_client:manage",
                    "audit_log:read", "ach_return_reason:manage", "data_export:run", "billing:read", "issued_item:write", "compliance:read")
CUSTOMER_PERMISSIONS = ("customer_banner:manage", "billing:read", "issued_item:write", "customer_user:manage", "customer_sso:manage",
                        "customer_api_client:manage", "compliance:read")


@dataclass
class Tile:
    key: str
    section: str
    title: str
    icon: str
    href: str
    description: str
    state: str = ""
    tone: str = ""  # "", "ok", "warn" or "danger"
    modal: bool = False
    keywords: str = ""
    state_url: str | None = None  # the state is fetched after the page loads (slow to work out)

    @property
    def search_text(self) -> str:
        return " ".join((self.title, self.description, self.state, self.keywords, self.section)).lower()


def can_see_panel(ctx: TenantContext) -> bool:
    permissions = CUSTOMER_PERMISSIONS if ctx.customer_id is not None else BANK_PERMISSIONS
    return any(p in ctx.permissions for p in permissions)


def _count(db: Session, stmt) -> int:
    return db.execute(stmt).scalar_one() or 0


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _minutes(value: int | None, default: int) -> str:
    minutes = value or default
    return f"{minutes // 60} h" if minutes % 60 == 0 and minutes >= 60 else f"{minutes} min"


def bank_tiles(db: Session, ctx: TenantContext) -> list[Tile]:
    from openclear.config import get_settings
    from openclear.domain.tenant import Tenant

    tenant = db.get(Tenant, ctx.tenant_id)
    settings = get_settings()
    has = ctx.permissions.__contains__
    tiles: list[Tile] = []
    add = tiles.append
    for permission, build in _BANK_BUILDERS:
        if has(permission):
            for tile in build(db, ctx, tenant, settings):
                add(tile)
    return tiles


# --- Bank tiles, by the permission each needs ------------------------------------------------------


def _tenant_tiles(db, ctx, tenant, settings) -> list[Tile]:
    from openclear.services import cutoff_service, decision_outbox_service, reminder_service, sso_service
    from openclear.services.siem import delivery

    contact_set = any((tenant.support_email, tenant.support_phone, tenant.website))
    from openclear.services import wizard_service

    done, required = wizard_service.bank_progress(db, ctx.tenant_id)
    wizard_state = "Done" if done == required else f"{done} of {required} done"
    policy_rules = sum(bool(x) for x in (tenant.password_require_uppercase, tenant.password_require_lowercase,
                                         tenant.password_require_number, tenant.password_require_symbol))
    origins = [o for o in (tenant.embed_allowed_origins or "").splitlines() if o.strip()]
    outbox = decision_outbox_service.get_outbox(db, ctx.tenant_id)
    sso_count = len(sso_service.list_connections(db, ctx.tenant_id, customer_id=None))
    try:
        reminders = reminder_service.describe(reminder_service.bank_offsets(tenant))
    except reminder_service.ReminderError:
        reminders = "Not valid; open to fix"
    destination = delivery.bank_destination(db, ctx.tenant_id)
    if destination is None:
        siem_state, siem_tone = "Not set", ""
    elif not destination.enabled:
        siem_state, siem_tone = "Paused", "warn"
    elif destination.failing_since is not None:
        siem_state, siem_tone = "Failing: " + (destination.last_error or "no answer")[:60], "danger"
    else:
        siem_state, siem_tone = delivery.senders.KINDS.get(destination.kind, destination.kind) + (
            " · delivering" if destination.last_success_at else " · waiting for the first event"), "ok"
    messages = [name for name, value in (("login", tenant.login_message), ("banner", tenant.banner_message)) if value]
    wsud_state = _wsud_form_state(db, ctx.tenant_id)
    return [
        Tile("branding", "Organization", "Branding", "palette", "/ui/settings/branding",
             "Your name, logo, favicon and accent color.", tenant.name, modal=True, keywords="logo color colour favicon name"),
        Tile("contact", "Organization", "Contact info", "contact_support", "/ui/settings/contact",
             "Support email, phone and address in every page's footer.", "Set" if contact_set else "Not set", modal=True,
             keywords="footer support email phone address website"),
        Tile("messages", "Organization", "Messages", "campaign", "/ui/settings/messages",
             "A message on your login page, and a banner for everyone signed in.",
             ("Showing " + " and ".join(messages) + " message") if messages else "None", modal=True,
             keywords="login banner announcement"),
        Tile("getting-started", "Organization", "Getting started", "checklist", "/ui/wizard/bank",
             "The checklist for setting up a new bank.", wizard_state, tone="" if wizard_state == "Done" else "warn",
             keywords="wizard setup onboarding"),
        Tile("password-policy", "Security & access", "Password policy", "password", "/ui/settings/password-policy",
             "Minimum length and required characters for new passwords.",
             f"{tenant.password_min_length}+ characters" + (f", {_plural(policy_rules, 'rule')}" if policy_rules else ""),
             modal=True, keywords="passwords length complexity"),
        Tile("security-keys", "Security & access", "Required security keys", "key", "/ui/settings/security-keys",
             "Require a security key or passkey for password sign-ins, with a grace period.",
             ("Required" if tenant.require_security_key else "Not required"), modal=True,
             keywords="mfa 2fa passkey webauthn second factor"),
        Tile("access-rules", "Security & access", "Lockout and inactive accounts", "block", "/ui/settings/access-rules",
             "Lock accounts after failed sign-ins; deactivate and remove access nobody uses.", _access_rules_state(tenant),
             modal=True, keywords="lockout locked failed attempts inactive dormant deactivate remove delete stale accounts"),
        Tile("timeouts", "Security & access", "Timeouts", "timer", "/ui/settings/timeouts",
             "How long sessions last, and how long a data export may run.",
             f"Idle {_minutes(tenant.access_token_expire_minutes, settings.jwt_access_token_expire_minutes)} · "
             f"session {_minutes(tenant.refresh_token_expire_minutes, settings.jwt_refresh_token_expire_minutes)}",
             modal=True, keywords="session idle sign out logout export timeout"),
        Tile("sso", "Security & access", "Single sign-on", "key", "/ui/admin/sso",
             "Sign bank staff in through your identity provider.", _plural(sso_count, "connection") if sso_count else "Off",
             keywords="sso saml oidc okta entra azure identity provider"),
        Tile("support-access", "Security & access", "Support access", "vpn_key", "/ui/settings/support-access",
             "Let a named support person in, for a reason and a set time.", _support_state(db, ctx.tenant_id),
             keywords="support help vendor access grant impersonate"),
        Tile("custom-fields", "Organization", "Custom fields", "list_alt", "/ui/settings/custom-fields",
             "Your own fields on customers and accounts: an officer code, an id for the core.", _custom_fields_state(db, ctx.tenant_id),
             keywords="custom fields officer code identifier erp core attribute"),
        Tile("incident-contacts", "Security & access", "Incident contacts", "notifications_active", "/ui/settings/incident-contacts",
             "Who hears of an incident at OpenClear's host, and your CEO and CIO as the fallback.",
             _incident_state(db, ctx.tenant_id, tenant), tone="warn" if not tenant.incident_contacts_confirmed_at else "",
             keywords="incident outage breach notice contact fdic nydfs 36 hours"),
        Tile("wsud-form", "Data & compliance", "Unauthorized debit statement", "gavel", "/ui/settings/wsud-form",
             "Your form for customers to report an unauthorized ACH debit: Markdown, HTML or a PDF.",
             wsud_state, tone="warn" if wsud_state == "Not set" else "",
             keywords="wsud unauthorized debit statement nacha form return"),
        Tile("outside-services", "Data & compliance", "Outside services", "extension", "/ui/settings/outside-services",
             "The outside services OpenClear uses for you, and when you last reviewed each.", modal=True,
             keywords="vendors third party providers tprm review"),
        Tile("standards", "Data & compliance", "Your standards", "rule", "/ui/settings/standards",
             "Your password and key-rotation standards, which the controls check against.",
             "Set" if tenant.standard_password_min_length and tenant.key_rotation_days else "Not set", modal=True,
             keywords="policy password keys rotation"),
        Tile("customer-approval", "Security & access", "New customer users", "how_to_reg", "/ui/settings/customer-approval",
             "Approve people your customers add before they can sign in.", "Bank approves" if tenant.approve_customer_users else "Off",
             modal=True, keywords="approval customers invitation onboarding pending"),
        Tile("segregation-of-duties", "Security & access", "Segregation of duties", "rule", "/ui/settings/segregation-of-duties",
             "Which duties one person mustn't hold together.", "Enforce" if tenant.sod_mode == "enforce" else "Warn",
             tone="ok" if tenant.sod_mode == "enforce" else "warn", keywords="sod maker checker conflicts"),
        Tile("dual-control", "Decisions & rules", "Dual control", "how_to_reg", "/ui/settings/dual-control",
             "A second person approves each pay or return decision.", "On" if tenant.require_dual_control else "Off",
             tone="ok" if tenant.require_dual_control else "", modal=True, keywords="maker checker approval four eyes"),
        Tile("cutoff", "Decisions & rules", "Decision cut-off", "schedule", "/ui/settings/cutoff",
             "When items must be decided by; decisions can change until then.", cutoff_service.describe(tenant), modal=True,
             keywords="cutoff deadline time zone holidays closed business day final"),
        Tile("reminders", "Decisions & rules", "Reminders", "notifications_active", "/ui/settings/reminders",
             "Email and text reminders before the cutoff for items still to decide.", reminders, modal=True,
             keywords="cutoff deadline notify notifications sms email"),
        Tile("fraud-model", "Decisions & rules", "Fraud scoring", "analytics", "/ui/settings/fraud-model",
             "An advisory score on each exception, from a model trained on your own decisions.",
             "On" if tenant.ml_scoring_enabled else "Off", tone="ok" if tenant.ml_scoring_enabled else "",
             keywords="ml machine learning score model"),
        Tile("decision-files", "Integrations", "Decision files", "description", "/ui/settings/decision-files",
             "Write each pay and return decision to a file for your core.",
             f"Every {outbox.interval_minutes} min" if outbox.enabled else "Off", tone="ok" if outbox.enabled else "",
             keywords="outbox core file csv"),
        Tile("embedding", "Integrations", "Embedding", "web", "/ui/settings/embedding",
             "Show OpenClear inside your online banking.", _plural(len(origins), "origin") if origins else "Off", modal=True,
             keywords="iframe online banking embed origins"),
        Tile("security-events", "Integrations", "Security event forwarding", "security", "/ui/settings/security-events",
             "Send sign-ins, lockouts and changes to your SIEM: syslog, Splunk or HTTPS.", siem_state, tone=siem_tone,
             modal=True, keywords="siem splunk syslog qradar sentinel elastic cef leef soc"),
        Tile("auto-import", "Integrations", "Auto-import", "drive_folder_upload", "/ui/settings/auto-import",
             "Files your bank's file transfer drops are imported automatically.",
             "On" if settings.auto_import_enabled else "Off", modal=True, keywords="dropbox sftp folder files"),
    ]


def _access_rules_state(tenant) -> str:
    rules = [f"deactivate after {tenant.deactivate_inactive_days} days" if tenant.deactivate_inactive else "",
             f"remove after {tenant.remove_inactive_days} days" if tenant.remove_inactive else ""]
    rules = [r for r in rules if r]
    return ("Inactive: " + ", ".join(rules)) if rules else "No automatic deactivation"


def _support_state(db, tenant_id) -> str:
    from openclear.domain.support_access_grant import SupportAccessGrant

    active = _count(db, select(func.count()).select_from(SupportAccessGrant).where(
        SupportAccessGrant.tenant_id == tenant_id, SupportAccessGrant.status.in_(("pending", "active"))))
    return f"{active} open" if active else "None"


def _admin_tiles(db, ctx, tenant, settings) -> list[Tile]:
    from openclear.services import change_request_service

    pending = len(change_request_service.pending(db, ctx.tenant_id))
    return [
        Tile("ml-models", "Decisions & rules", "ML models", "model_training", "/ui/admin/ml-models",
             "Training, versions and activation of your scoring models.", keywords="machine learning retrain"),
        Tile("changes", "Security & access", "Changes awaiting approval", "fact_check", "/ui/admin/changes",
             "Settings changes waiting for a second person.", _plural(pending, "waiting", "waiting") if pending else "None",
             tone="warn" if pending else "", keywords="approve change requests"),
        Tile("model-risk", "Data & compliance", "Model risk", "science", "/ui/admin/model-risk",
             "Replay a past file through today's rules; check synthetic fraud is caught.", keywords="replay anomaly mrm validation"),
        Tile("plugins", "Integrations", "Plugins", "extension", "/ui/admin/plugins",
             "What extends this installation: networks, file formats, OCR and storage.", keywords="extensions"),
    ]


def _user_tiles(db, ctx, tenant, settings) -> list[Tile]:
    from openclear.domain.tenant_membership import TenantMembership

    active = _count(db, select(func.count()).select_from(TenantMembership).where(
        TenantMembership.tenant_id == ctx.tenant_id, TenantMembership.is_active.is_(True)))
    return [Tile("users", "Security & access", "Users", "person", "/ui/users", "Who can sign in, and their groups.",
                 _plural(active, "active user"), keywords="people staff accounts invite"),
            _access_review_tile(db, ctx)]


def _access_review_tile(db, ctx) -> Tile:
    """Access reviews (CONTROLS_PLAN.md CT3), for the bank or a self-managing customer."""
    from openclear.services import access_review_service

    still_open = access_review_service.open_review(db, ctx.tenant_id, ctx.customer_id)
    last = next((r for r in access_review_service.reviews(db, ctx.tenant_id, ctx.customer_id, limit=5) if r.completed_at), None)
    state = "Open" if still_open else (f"Last {last.completed_at:%Y-%m-%d}" if last else "None yet")
    return Tile("access-reviews", "Security & access", "Access reviews", "fact_check", "/ui/access-reviews",
                "Confirm each person's access at least once a quarter, and remove what's no longer needed.", state,
                tone="warn" if still_open or not last else "", keywords="review recertification access users quarterly")


def _group_tiles(db, ctx, tenant, settings) -> list[Tile]:
    from openclear.services import security_group_service

    groups = len(security_group_service.list_security_groups(db, ctx.tenant_id))
    return [Tile("security-groups", "Security & access", "Security groups", "admin_panel_settings", "/ui/security-groups",
                 "What each group of people may do.", _plural(groups, "group"), keywords="roles permissions")]


def _integration_tiles(db, ctx, tenant, settings) -> list[Tile]:
    from openclear.services import api_client_service, webhook_service

    clients = sum(1 for c in api_client_service.list_clients(db, ctx.tenant_id) if c.revoked_at is None)
    endpoints = sum(1 for e in webhook_service.list_endpoints(db, ctx.tenant_id) if e.is_active)
    return [
        Tile("api-clients", "Integrations", "API clients", "vpn_key", "/ui/admin/api-clients",
             "Credentials for your core and online banking to call the API.", _plural(clients, "client") if clients else "None",
             keywords="oauth client credentials secret api"),
        Tile("webhooks", "Integrations", "Webhooks", "webhook", "/ui/admin/webhooks",
             "Tell your systems when an exception is raised or decided.", _plural(endpoints, "endpoint") if endpoints else "None",
             keywords="callbacks events notify"),
        Tile("integration", "Integrations", "Integration checklist", "checklist", "/ui/admin/integration",
             "What your core or online banking integration has shown it does.", keywords="conformance certification"),
    ]


def _audit_tiles(db, ctx, tenant, settings) -> list[Tile]:
    return [
        Tile("controls", "Data & compliance", "Controls", "verified_user", "/ui/admin/controls",
             "Each control's live status, with its evidence.", "Checking…", state_url="/ui/admin/controls/summary",
             keywords="compliance evidence audit ffiec soc pass fail"),
        Tile("action-log", "Data & compliance", "Action log", "history", "/ui/audit-log",
             "Every change, signed and verifiable.", keywords="audit trail history"),
    ]


def _return_reason_tiles(db, ctx, tenant, settings) -> list[Tile]:
    from openclear.services import ach_return_reason_service, check_return_reason_service

    checks = len(check_return_reason_service.list_check_return_reasons(db, ctx.tenant_id))
    ach = len(ach_return_reason_service.list_ach_return_reasons(db, ctx.tenant_id, active_only=True))
    return [
        Tile("check-return-reasons", "Decisions & rules", "Check return reasons", "assignment_return", "/ui/check-return-reasons",
             "The reasons reviewers choose from when returning a check.", _plural(checks, "reason"),
             keywords="return codes x9 refer to maker"),
        Tile("ach-return-reasons", "Decisions & rules", "ACH return reasons", "assignment_return", "/ui/ach-return-reasons",
             "The NACHA return codes reviewers choose from for ACH.", _plural(ach, "reason"), keywords="nacha r codes r05 r10"),
    ]


def _export_tiles(db, ctx, tenant, settings) -> list[Tile]:
    return [Tile("data-export", "Data & compliance", "Data export", "download", "/ui/settings/data-export",
                 "An archive of everything belonging to your organization.", keywords="backup migrate offboard archive")]


def _billing_tiles(db, ctx, tenant, settings) -> list[Tile]:
    if ctx.customer_id is not None:
        return []
    return [Tile("billing", "Data & compliance", "Billing", "request_quote", "/ui/billing",
                 "Fees, customer billing terms and statements.", keywords="fees invoice statement")]


def _layout_tile(db, ctx) -> Tile:
    from openclear.bulk_import import layouts
    from openclear.services import import_layout_service

    if ctx.customer_id is not None:
        rows = import_layout_service.visible(db, ctx.tenant_id, ctx.customer_id)
        default = next((r for r in rows if r.is_default and r.customer_id == ctx.customer_id), None) or next((r for r in rows if r.is_default), None)
        state = (_plural(len(rows), "layout") + (f" · default {default.name}" if default else "")) if rows else "None"
        return Tile("layouts", "Organization", "Issue-file layouts", "description", "/ui/layouts",
                    "How to read issued-check files in your own columns or a fixed-width format.", state,
                    keywords="issue file arp mapping columns fixed width excel upload import")
    # The bank's staff: issued-check and posted-check layouts, as far as they may build each.
    kinds = [k for k, permission in import_layout_service.PERMISSION.items() if permission in ctx.permissions]
    rows = [r for k in kinds for r in import_layout_service.visible(db, ctx.tenant_id, None, k)]
    posted_default = layouts.PAID_ITEMS in kinds and import_layout_service.default_for(db, ctx.tenant_id, None, layouts.PAID_ITEMS)
    state = (_plural(len(rows), "layout") + (f" · posted checks: {posted_default.name}" if posted_default else "")) if rows else "None"
    return Tile("layouts", "Integrations", "File layouts", "description", "/ui/layouts",
                "How to read issued-check files and your core's posted-check file in their own columns or a fixed-width format.",
                state, keywords="issue file arp posted paid presented core mapping columns fixed width trailer excel upload import")


def _layout_tiles(db, ctx, tenant, settings) -> list[Tile]:
    return [_layout_tile(db, ctx)]


def _posted_layout_tiles(db, ctx, tenant, settings) -> list[Tile]:
    """The same tile for someone who builds only posted-check layouts."""
    return [] if "issued_item:write" in ctx.permissions else [_layout_tile(db, ctx)]


def _cuec_tile(db, ctx, *_args) -> Tile:
    """CUECs and attestations (CONTROLS_PLAN.md CT4): the bank's, or a customer's own responsibilities."""
    from openclear.services import attestation_service

    waiting = sum(1 for a in attestation_service.attestations(db, ctx.tenant_id, ctx.customer_id, limit=200) if a.status == "prepared")
    title = "Your responsibilities" if ctx.customer_id else "CUECs"
    description = ("What OpenClear relies on you to do, mapped to your own controls and attested each period." if ctx.customer_id
                   else "What OpenClear relies on you, your customers and your host to do: mappings and signed attestations.")
    return Tile("cuecs", "Data & compliance", title, "fact_check", "/ui/compliance/cuecs", description,
                f"{waiting} to accept" if waiting else "", tone="warn" if waiting else "", keywords="cuec attestation grc soc nist responsibilities")


def _compliance_tiles(db, ctx, tenant, settings) -> list[Tile]:
    return [_cuec_tile(db, ctx)]


_BANK_BUILDERS: list[tuple[str, Callable]] = [
    ("tenant:manage", _tenant_tiles),
    ("admin:manage", _admin_tiles),
    ("user:manage", _user_tiles),
    ("security_group:manage", _group_tiles),
    ("api_client:manage", _integration_tiles),
    ("ach_return_reason:manage", _return_reason_tiles),
    ("audit_log:read", _audit_tiles),
    ("compliance:read", _compliance_tiles),
    ("data_export:run", _export_tiles),
    ("billing:read", _billing_tiles),
    ("issued_item:write", _layout_tiles),
    ("paid_item:write", _posted_layout_tiles),
]


# --- A customer's own tiles -----------------------------------------------------------------------


def customer_tiles(db: Session, ctx: TenantContext) -> list[Tile]:
    from openclear.domain.customer import Customer
    from openclear.domain.tenant import Tenant
    from openclear.services import reminder_service

    customer = db.get(Customer, ctx.customer_id)
    tiles = []
    if "customer_banner:manage" in ctx.permissions:
        tenant = db.get(Tenant, ctx.tenant_id)
        try:
            timing = reminder_service.describe(reminder_service.customer_offsets(tenant, customer))
        except reminder_service.ReminderError:
            timing = "Not valid; open to fix"
        follows = customer.reminder_offsets_minutes is None
        tiles += [
            Tile("reminders", "Organization", "Reminders", "notifications_active", "/ui/settings/reminders",
                 "Email and text reminders before the cutoff for items still to decide.",
                 timing + (" (your bank's)" if follows and timing != "Off" else ""), modal=True,
                 keywords="cutoff deadline notify notifications sms email"),
            Tile("banner", "Organization", "Banner message", "campaign", f"/ui/customers/{ctx.customer_id}/banner",
                 "A message at the top of every page for your own people.", "Showing" if customer.banner_message else "None",
                 keywords="announcement message"),
        ]
    if "customer_user:manage" in ctx.permissions:
        from openclear.services import customer_people_service

        active = sum(p.membership.is_active for p in customer_people_service.list_people(db, ctx.tenant_id, ctx.customer_id))
        waiting = len(customer_people_service.pending_invitations(db, ctx.tenant_id, ctx.customer_id))
        tiles.append(Tile("people", "Security & access", "People", "groups", "/ui/people",
                          "Invite your people, choose their roles and accounts, and remove access when they leave.",
                          f"{active} active" + (f", {waiting} invited" if waiting else ""), keywords="users invite team staff"))
        tiles.append(_access_review_tile(db, ctx))
    if "customer_sso:manage" in ctx.permissions:
        from openclear.services import sso_service

        working = len(sso_service.get_login_connections(db, ctx.tenant_id, customer_id=ctx.customer_id))
        tiles.append(Tile("sign-on", "Security & access", "Sign-on", "login", "/ui/sign-on",
                          "Connect your identity provider, verify your email domains, and choose whether passwords still work.",
                          (f"{working} connection{'s' if working != 1 else ''}" if working else "Passwords only")
                          + ("" if customer.password_login_enabled or not working else ", passwords off"),
                          keywords="sso single sign-on saml oidc okta entra azure scim domain password"))
    if "customer_api_client:manage" in ctx.permissions:
        from openclear.services import api_client_service

        active = sum(c.revoked_at is None for c in api_client_service.list_customer_clients(db, ctx.tenant_id, ctx.customer_id))
        tiles.append(Tile("api-clients", "Integrations", "API clients", "vpn_key", "/ui/api-clients",
                          "Credentials for your own systems, such as your accounting system, to send issued checks or read decisions.",
                          f"{active} active" if active else "None", keywords="api integration erp accounting credentials key"))
    if "issued_item:write" in ctx.permissions:
        tiles.append(_layout_tile(db, ctx))
    if "compliance:read" in ctx.permissions:
        tiles.append(_cuec_tile(db, ctx))
    if "billing:read" in ctx.permissions:
        tiles.append(Tile("billing-statement", "Data & compliance", "Billing statement", "request_quote", "/ui/billing/statement",
                          "What your bank charges you for positive pay.", keywords="fees invoice"))
    return tiles


def plugin_tiles(ctx: TenantContext) -> list[Tile]:
    """Plugins' tiles (`settings.tile`; plugins/ui_slots.py) this bank turned on, under
    Integrations. A tile's `html` is its description (as text), its first action where it leads."""
    import re

    from openclear.plugins import ui_slots

    out = []
    for item in ui_slots.render_slot(None, ctx, "settings.tile", {}):
        if not item.content.actions:
            continue
        description = re.sub(r"<[^>]+>", "", item.html).strip()
        out.append(Tile(f"plugin-{item.entry.name}", "Integrations", item.content.title, "extension", item.content.actions[0].href,
                        description, item.content.state, keywords=f"plugin {item.plugin}"))
    return out


def tiles_for(db: Session, ctx: TenantContext) -> list[Tile]:
    return (customer_tiles(db, ctx) if ctx.customer_id is not None else bank_tiles(db, ctx)) + plugin_tiles(ctx)


def grouped(tiles: list[Tile]) -> list[tuple[str, list[Tile]]]:
    return [(section, [t for t in tiles if t.section == section]) for section in SECTIONS if any(t.section == section for t in tiles)]


def tile_for(db: Session, ctx: TenantContext, key: str) -> Tile | None:
    return next((t for t in tiles_for(db, ctx) if t.key == key), None)


def _custom_fields_state(db, tenant_id) -> str:
    from openclear.services import custom_field_service

    counts = {owner: len(custom_field_service.list_fields(db, tenant_id, owner)) for owner in ("customer", "account")}
    if not any(counts.values()):
        return "None yet"
    return ", ".join(f"{n} on {owner}s" for owner, n in counts.items() if n)


def _incident_state(db, tenant_id, tenant) -> str:
    from openclear.services import incident_service

    named = [c for c in incident_service.contacts(db, tenant_id) if c.kind == "contact"]
    if not named:
        return "Not set"
    return f"{len(named)} contact{'s' if len(named) != 1 else ''}, confirmed {tenant.incident_contacts_confirmed_at:%Y-%m-%d}" \
        if tenant.incident_contacts_confirmed_at else f"{len(named)} contact{'s' if len(named) != 1 else ''}"


def _wsud_form_state(db, tenant_id) -> str:
    from openclear.services import bank_form_service

    form = bank_form_service.get(db, tenant_id)
    return {"markdown": "Written (Markdown)", "html": "Written (HTML)", "pdf": "A PDF"}.get(form.format, "Set") if form else "Not set"
