# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid
from openclear.domain.billing import BillingMethod


class MlModelSource(str, enum.Enum):
    """Which fraud-scoring model a bank uses (FIX_PLAN.md Phase 5; ml/predict.py).

    SHARED: the network model trained on every participating (SHARED) bank's decisions,
    run by the platform operator. Useful from day one.
    PRIVATE: a bank-only model trained on this bank's own decisions, run by its own
    admins. Its data never leaves it. Seeded from a copy of the shared model at the moment
    of switching, so scoring never has a gap (services/tenant_ml_service.py)."""

    SHARED = "shared"
    PRIVATE = "private"


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    require_dual_control: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # False only makes sense once at least one active bank-wide SsoConnection exists —
    # enforced in services/sso_service.py, not here, to keep this a plain column like
    # every other tenant-level toggle.
    password_login_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
    # tenant-OR-customer per boolean field, by construction. Defaults are deliberately
    # permissive (no complexity required) so existing behavior is unaffected until a
    # tenant admin opts in.
    password_min_length: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
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
    # A vendor's self-service sandbox (SDK_PLAN.md P4; services/sandbox_service.py): who asked
    # for it, and when it's wiped.
    is_sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    sandbox_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sandbox_contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Fraud-scoring model choice (services/tenant_ml_service.py). Every bank starts on the
    # shared model. `ml_private_switch_allowed_at` is the 90-day lock for new banks: set at
    # creation (services/provisioning_service.py), NULL (no lock) for banks that existed
    # before this setting did. `ml_shared_consent_*` records who acknowledged the
    # data-pooling disclosure and when (NULL for banks pooled before consent was recorded).
    ml_model_source: Mapped[MlModelSource] = mapped_column(
        Enum(MlModelSource, name="ml_model_source", native_enum=False, length=10),
        nullable=False,
        default=MlModelSource.SHARED,
    )
    ml_source_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ml_source_changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    ml_private_switch_allowed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ml_shared_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ml_shared_consent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True)
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
