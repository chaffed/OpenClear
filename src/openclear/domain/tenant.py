# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid
from openclear.domain.billing import BillingMethod


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    require_dual_control: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # New people a customer's side adds (an invitation, its own sign-on or SCIM) wait for the
    # bank's approval (MOBILE_PLAN.md R6; services/customer_approval_service.py). Off by default;
    # each customer can follow it or override it (Customer.approve_users).
    approve_customer_users: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Control deviations (CONTROLS_PLAN.md CT2; services/control_results_service.py): how long a
    # check may stay at FAIL before it counts against the control (CT-D6), and the checks whose
    # WARN also opens a deviation (every check's FAIL does).
    control_grace_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24, server_default="24")
    control_warn_deviations: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    # The bank's own standards (CONTROLS_PLAN.md CT3), which the password_policy and key_age checks
    # hold OpenClear to: the shortest password it allows anywhere, and how often keys are rotated.
    standard_password_min_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key_rotation_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # When the bank last reviewed each outside service OpenClear uses for it (services/outside_services.py):
    # {service key: {"at": ISO time, "by": email, "note": text}}.
    outside_service_reviews: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    # CUEC attestations (CONTROLS_PLAN.md CT4): the period they cover (monthly, quarterly or yearly), and
    # whether whoever prepares a manual attestation must leave accepting it to someone else (CT-D9).
    attestation_period: Mapped[str] = mapped_column(String(10), nullable=False, default="quarterly", server_default="quarterly")
    attest_second_person: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Ask the bank's customers to attest their own CUECs each year (CT-D13): once a year has
    # ended, its request goes to each customer's people who can attest; the year asked for last.
    ask_customers_to_attest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    customers_asked_for_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Lockout after failed password sign-ins (auth/login_service.py): None takes the installation's
    # login_max_failed_attempts and login_lockout_minutes; lockout_until_unlocked keeps the account
    # locked until an administrator unlocks it.
    login_max_failed_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    login_lockout_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lockout_until_unlocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Inactive accounts (services/inactivity_service.py): deactivate a person's access after so
    # many days without signing in to this bank, and remove it after so many; each off by default.
    deactivate_inactive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    deactivate_inactive_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90, server_default="90")
    remove_inactive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    remove_inactive_days: Mapped[int] = mapped_column(Integer, nullable=False, default=365, server_default="365")
    # A second admin for settings changes (DUAL_CONTROL_PLAN.md, services/change_request_service.py):
    # sensitive settings (tier B) and widening people's access. Turning a protection off needs one
    # whatever these say. Turned on only if someone else can approve (DC-D16).
    sensitive_changes_need_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    access_changes_need_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Every customer's sensitive changes (its sign-on, API clients, new administrators) need a
    # second person: another of its administrators, or the bank (DUAL_CONTROL_PLAN.md DC3).
    customer_changes_need_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # False only makes sense once at least one active bank-wide SsoConnection exists —
    # enforced in services/sso_service.py, not here, to keep this a plain column like
    # every other tenant-level toggle.
    password_login_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Required security keys (OWASP_PLAN.md OW-D18–D20; auth/security_key_policy.py): everyone who
    # signs in here with a password must also use a security key, after a grace period counted
    # from when it was turned on. A customer can turn it on for itself as well.
    require_security_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    security_key_grace_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14, server_default="14")
    security_key_required_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # API clients must authenticate with a signed assertion (private_key_jwt), not a secret (OW-D26).
    api_clients_key_pair_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    stale_date_threshold_days: Mapped[int] = mapped_column(Integer, nullable=False, default=180)
    # Nullable = "use the app's global default" (config.Settings.jwt_access/refresh_
    # token_expire_minutes) — see auth/security.py::create_token and
    # services/tenant_service.py::set_session_timeouts. Doesn't affect the short-lived
    # mfa_pending token, which stays fixed regardless of tenant.
    access_token_expire_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    refresh_token_expire_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Same nullable-override pattern as the two above — None means "use
    # config.Settings.data_export_timeout_seconds" — see
    # services/data_export_service.py::run_export_job and
    # services/tenant_service.py::set_data_export_timeout.
    data_export_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Password policy applied at account-creation time (services/user_service.py::
    # create_user_with_membership is the only place a password is ever set — there's no
    # self-service change/reset flow) — see auth/password_policy.py. A customer's own
    # policy (domain/customer.py) can only add to this, never weaken it: the effective
    # policy for a user is always max(tenant, customer) per numeric field and
    # tenant-OR-customer per boolean field, by construction. No composition rules by default
    # (ASVS 5.0 6.2.5); a bank may still turn them on.
    # 15 for banks created from 1.22 (NIST 800-63B-4 for single-factor sign-in; OWASP_PLAN.md
    # OW-D16); earlier banks keep the 8 they were created with.
    password_min_length: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    password_require_uppercase: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_require_lowercase: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_require_number: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_require_symbol: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # At most one tenant ever has this set (enforced at the application level in
    # services/demo_tenant_service.py, not a DB constraint) — the persistent sales-demo
    # tenant. Gates the reactive reset-on-idle check in the login flow and the manual
    # "reset now" admin action; never affects any other tenant's behavior.
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Segregation of duties (auth/sod.py, COMPLIANCE_PLAN.md C1): "warn" logs a breach and lets it
    # through; "enforce" refuses it (403 ERR_SOD_VIOLATION). Logged either way.
    sod_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="warn", server_default="warn")
    # A vendor's self-service sandbox (SDK_PLAN.md P4; services/sandbox_service.py): who asked
    # for it, and when it's wiped.
    is_sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    sandbox_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sandbox_contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Advisory fraud scoring (SIMPLIFY_PLAN.md SP-D10): off until the bank turns it on. Then it
    # trains its own model from its own final decisions and shows the score beside each item;
    # the score never decides one.
    ml_scoring_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Branding — local disk paths (see web/branding_storage.py), never the blob itself in
    # the row, same pattern as CheckImage.front_image_path. All nullable: unset means "use
    # the app's plain defaults" everywhere this is rendered.
    logo_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    logo_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    favicon_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    favicon_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    accent_color: Mapped[str | None] = mapped_column(String(7), nullable=True)

    # Contact info — shown in the page footer (templates/base.html), configured on
    # /ui/settings. All nullable/free-text, same shape (and same lack of format
    # validation) as Customer's own identical contact block — this is the bank's own
    # info, not a business client's. Unlike branding above, there's no "app default" to
    # fall back to: unset simply means the footer doesn't render at all (see
    # services/tenant_service.py::TenantBranding).
    support_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    support_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Markdown source (validated/normalized at save time by services/message_content.py,
    # rendered+sanitized at display time by web/templates.py::render_markdown), never raw
    # HTML. login_message shows on this tenant's own login page (templates/auth/login.html,
    # resolved pre-auth via TenantBranding) — e.g. scheduled-maintenance notices.
    # banner_message shows in the persistent banner at the top of every page once logged
    # in (templates/partials/_banner.html), to every one of this tenant's users, bank-wide
    # and customer-scoped alike. Both nullable: unset means nothing renders. Text, not a
    # capped String -- a message may embed a small base64-encoded image
    # (services/message_content.py); the real size limits (150KB/image, 500KB/message)
    # are enforced in Python, not the column type.
    login_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    banner_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Embedding (SDK_PLAN.md P2; services/embed_service.py): the https origins of the bank's
    # online banking that may frame OpenClear's pages, one per line. Empty: no embedding.
    embed_allowed_origins: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Reminders before the cutoff (UX_PLAN.md U3; services/reminder_service.py): minutes
    # before each exception's decision deadline, comma-separated ("120,30"). Null uses the
    # installation's default (config.Settings.reminder_offsets_minutes); "" turns them off.
    reminder_offsets_minutes: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The decision cut-off (UI_PLAN.md UI-D13; services/cutoff_service.py): each network's time
    # of day ({"check": "11:00", "ach": "15:00"}) in the bank's time zone (an IANA name), by
    # which items are decided; a customer may have its own (CustomerDispositionSetting.cutoff_time).
    # An item's deadline is the first cut-off on a business day at least cutoff_min_review_minutes
    # after it arrives. Without a cut-off for a network, deadlines come from the response window.
    time_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_cutoffs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    cutoff_min_review_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=120, server_default="120")
    # The bank opens on Federal Reserve holidays (REGULATORY_PLAN.md RG1): they're then banking days
    # under the UCC, so a check's return deadline (its midnight deadline) counts them.
    open_on_federal_holidays: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # When an administrator last confirmed the bank's incident contacts (domain/incident.py;
    # REGULATORY_PLAN.md RG3): the control check incident_contacts asks for it yearly.
    incident_contacts_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Email the daily open-items snapshot to the incident contacts (services/continuity_snapshot.py; CONTINUITY_PLAN.md BC-D5).
    continuity_snapshot_emailed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Offboarding (plans/OFFBOARDING_PLAN.md): when the service provider started it, so the bank's
    # data can be deleted once the wait is over; and when it was deleted (the row stays, inactive,
    # so the slug is never reused).
    offboarding_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set while the bank's data is on legal hold (a regulator's request, litigation): it can't be deleted.
    legal_hold_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # When the bank last read its security events through the pull API (SIEM_PLAN.md S5):
    # with no destination configured, the "Security events delivered" control warns once
    # nobody has read them for a day.
    security_event_feed_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Billing (domain/billing.py, BILLING_PLAN.md). How customers with no billing terms of
    # their own are billed, and how the bank's own accounts (not assigned to a customer)
    # and bank-wide services (e.g. SMS) are billed. bank_billing_account_id is validated in
    # code, not by a foreign key (account already references tenant).
    customer_default_billing_method: Mapped[BillingMethod] = mapped_column(
        Enum(BillingMethod, name="customer_default_billing_method", native_enum=False, length=20),
        nullable=False, default=BillingMethod.ANALYSIS,
    )
    bank_billing_method: Mapped[BillingMethod] = mapped_column(
        Enum(BillingMethod, name="bank_billing_method", native_enum=False, length=20),
        nullable=False, default=BillingMethod.EXEMPT,
    )
    bank_billing_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
