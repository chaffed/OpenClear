# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""A persistent, fully-functioning sales-demo tenant that resets to its pristine seeded
state once its own (short) session window has elapsed with no login — see
`maybe_reset_if_demo_idle_by_slug`, called from the login flow *before* credentials are
even checked, and the manual "reset now" admin action (web/routers/admin.py).

Safety notes baked into the design, not incidental:
- `reset_demo_tenant`/`_wipe_tenant_children` only ever operate on the one tenant flagged
  `is_demo=True` — looked up fresh each time, never passed in from a caller, so there's no
  path for this to accidentally target a real tenant.
- The demo's ML model is trained and activated *per-customer* (`customer_id=` the demo's
  own seeded customer), never the bare global model — `MlModel` has no `tenant_id` column
  at all (confirmed by reading `domain/ml_model.py`), so the "global" model is genuinely
  shared across every tenant in the database. Training it from synthetic demo data on every
  reset would silently corrupt real production scoring. A per-customer model demonstrates
  the exact same feature with zero cross-tenant risk.
- `_wipe_tenant_children` purges on-disk files (check images, bulk uploads, branding
  assets, ML artifacts) as well as DB rows — see `_purge_tenant_files`. Without this, a
  public demo tenant's uploaded check images (and any real PII an OCR run extracted from
  one) would outlive their own already-deleted DB row indefinitely, bounded only by disk
  space rather than the idle-reset window.
"""

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from openclear import storage
from openclear.config import get_settings
from openclear.db.base import Base
from openclear.db.tenancy import TenantContext, bind_session_to_tenant
from openclear.domain.ach_transaction import AchTransactionType
from openclear.domain.customer import Customer
from openclear.domain.decision import DecisionOutcome
from openclear.domain.ml_model import MlModel
from openclear.domain.tenant import Tenant
from openclear.domain.user import User
from openclear.ml.registry import activate_model
from openclear.ml.train import InsufficientTrainingData, train_model
from openclear.networks.ach.ingestion import AchTransactionSubmission, ingest_ach_transaction
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import (
    account_service,
    ach_authorization_service,
    ach_return_reason_service,
    audit_log_service,
    customer_service,
    decision_service,
    issued_item_service,
    provisioning_service,
    security_group_service,
    stop_payment_service,
    tenant_service,
    user_service,
)

logger = logging.getLogger(__name__)

DEMO_TENANT_NAME = "Riverside Community Bank"
DEMO_TENANT_SLUG = "riverside-bank"
DEMO_ADMIN_EMAIL = "admin@riversidebank.example.com"

# (security group name, email local-part) — mirrors scripts/generate_screenshots.py's own
# extra_users list, since this is the same demo dataset.
_EXTRA_DEMO_USERS: list[tuple[str, str]] = [
    ("Preparer", "sarah.preparer"),
    ("Approver", "james.approver"),
    ("Viewer", "linda.viewer"),
    ("Bookkeeper", "morgan.bookkeeper"),
]
DEMO_USER_EMAILS: list[str] = [DEMO_ADMIN_EMAIL] + [
    f"{local_part}@riversidebank.example.com" for _group, local_part in _EXTRA_DEMO_USERS
]

_PAYEES = [
    "Northgate Office Supply", "Summit Roofing LLC", "Dana Reyes", "Cascade Landscaping",
    "Harbor View Consulting", "Pinecrest Utilities", "Bright Path Marketing", "Ferris Logistics",
]


class DemoTenantNotConfigured(Exception):
    """Raised by reset_demo_tenant() when there's no demo tenant to reset, or the demo
    password isn't configured — never falls back to guessing or creating one implicitly."""


def _decision_ctx(tenant: Tenant, user_id: uuid.UUID) -> TenantContext:
    # decision_service.decide() only reads ctx.tenant_id/user_id (not permissions or
    # branding) -- placeholder values are fine here, same as
    # scripts/generate_screenshots.py's own _decision_ctx.
    return TenantContext(
        tenant_id=tenant.id, user_id=user_id, security_group_id=uuid.uuid4(), permissions=frozenset(),
        tenant_slug=tenant.slug, tenant_name=tenant.name, accent_color=None, has_logo=False, has_favicon=False,
        customer_id=None, customer_name=None,
    )


def get_demo_tenant(session: Session) -> Tenant | None:
    return session.execute(select(Tenant).where(Tenant.is_demo.is_(True))).scalar_one_or_none()


def _seed_check_activity(
    session: Session, tenant: Tenant, account, *, preparer_user_id: uuid.UUID, approver_user_id: uuid.UUID,
    prefix: str, count: int, decide_upto: int,
) -> int:
    """Seeds `count` issued items against `account`, presents a paid item for each (some
    clean, most deliberately off by $25 so they land in the exception queue), and decides
    the first `decide_upto` resulting exceptions. Returns how many were actually decided.
    Reused for both the tenant-wide account (populates the open review queue) and the
    demo customer's own account (needs >= MIN_DECISIONS_TO_TRAIN=10 decided exceptions of
    its own for the per-customer ML model to train on)."""
    issue_date = date(2026, 6, 1)
    decided = 0
    for i in range(count):
        check_number = f"{prefix}{i:03d}"
        issued_amount = Decimal("100.00") + Decimal(i * 37 % 900)
        issued_item_service.create_issued_item(
            session, tenant.id,
            issued_item_service.IssuedItemInput(
                account_id=account.id, check_number=check_number, amount=issued_amount,
                payee_name=_PAYEES[i % len(_PAYEES)], issue_date=issue_date + timedelta(days=i % 10),
            ),
            submitted_by_user_id=preparer_user_id,
        )
        session.commit()

        presented_amount = issued_amount if i % 4 == 0 else issued_amount + Decimal("25.00")
        paid_item = ingest_paid_item(
            session, tenant.id,
            PaidItemSubmission(
                account_id=account.id, check_number=check_number, presented_amount=presented_amount,
                presented_date=issue_date + timedelta(days=i % 10 + 3),
            ),
        )
        session.commit()

        if presented_amount != issued_amount and i < decide_upto:
            exception = ExceptionRepository(session, tenant.id).list(source_item_id=paid_item.id)[0]
            outcome = DecisionOutcome.RETURN if i % 3 == 0 else DecisionOutcome.PAY
            decision_service.decide(
                session, tenant.id, exception.id, _decision_ctx(tenant, approver_user_id),
                outcome=outcome, reason_code="amount_mismatch", notes="Reviewed against issued amount.",
            )
            session.commit()
            decided += 1
    return decided


def seed_demo_content(
    session: Session, tenant: Tenant, admin_user: User, *, password: str, email_domain: str = "riversidebank.example.com",
    customer_contact_email: str = "dana@maplestreetdental.example.com", tenant_name: str = DEMO_TENANT_NAME,
) -> None:
    """The full realistic dataset -- accounts, a customer, issued/paid items (tenant-wide
    and customer-scoped), a stop payment, ACH authorizations/transactions, and a
    per-customer (never global -- see module docstring) ML model. Assumes `tenant` and
    `admin_user`+membership already exist; called by both ensure_demo_tenant (first run)
    and reset_demo_tenant (every reset after). Assumes the baseline "check"/"ach"
    PaymentNetwork rows already exist (seeded once by a migration, not per-tenant)."""
    tenant_service.update_tenant_contact_info(
        session, tenant.id, support_email="support@riversidebank.example.com", support_phone="(555) 867-5309",
        website="https://riversidebank.example.com", address_line1="42 River Street", address_line2=None,
        city="Springfield", state="IL", postal_code="62701",
    )
    session.commit()

    groups = {g.name: g for g in security_group_service.list_security_groups(session, tenant.id)}
    users: dict[str, User] = {"admin": admin_user}
    for group_name, local_part in _EXTRA_DEMO_USERS:
        group = groups.get(group_name)
        if group is None:
            continue
        users[group_name.lower()] = user_service.create_user_with_membership(
            session, tenant.id, email=f"{local_part}@{email_domain}", password=password,
            security_group_id=group.id,
        )
    session.commit()
    for name, user in users.items():
        if name == "admin":
            continue
        audit_log_service.record_action(
            session, tenant.id, actor_user_id=admin_user.id, channel="web", action="user.create",
            summary=f"Added user {user.email}", resource_type="user", resource_id=user.id,
        )
    session.commit()

    logger.info("Seeding demo accounts and a customer...")
    operating = account_service.create_account(
        session, tenant.id, account_service.AccountInput(account_number="1000234561", name="Operating Account")
    )
    payroll = account_service.create_account(
        session, tenant.id, account_service.AccountInput(account_number="1000234562", name="Payroll Account")
    )
    customer = customer_service.create_customer(
        session, tenant.id,
        customer_service.CustomerInput(
            customer_number="CUST-1001", name="Maple Street Dental", primary_contact_name="Dana Reyes",
            email=customer_contact_email,
        ),
    )
    customer_account = account_service.create_account(
        session, tenant.id,
        account_service.AccountInput(account_number="1000234563", name="Maple Street Dental Operating", customer_id=customer.id),
    )
    session.commit()

    logger.info("Seeding tenant-wide issued/paid items (some clean, some exceptions)...")
    decided_tenant_wide = _seed_check_activity(
        session, tenant, operating, preparer_user_id=users["preparer"].id, approver_user_id=users["approver"].id,
        prefix="10", count=26, decide_upto=22,
    )
    logger.info("  %d tenant-wide exceptions decided, some left open for the review queue.", decided_tenant_wide)

    logger.info("Seeding the demo customer's own issued/paid items (for its per-customer ML model)...")
    decided_customer = _seed_check_activity(
        session, tenant, customer_account, preparer_user_id=users["preparer"].id, approver_user_id=users["approver"].id,
        prefix="20", count=16, decide_upto=16,
    )

    logger.info("Seeding a stop payment and the resulting exception...")
    issue_date = date(2026, 6, 1)
    stop_payment_service.create_stop_payment(
        session, tenant.id,
        stop_payment_service.StopPaymentInput(
            account_id=operating.id, check_number="9001", amount=None, effective_date=issue_date,
            expiration_date=issue_date + timedelta(days=180), reason="Lost in the mail per payee request.",
        ),
        created_by_user_id=users["approver"].id,
    )
    session.commit()
    ingest_paid_item(
        session, tenant.id,
        PaidItemSubmission(
            account_id=operating.id, check_number="9001", presented_amount=Decimal("450.00"),
            presented_date=issue_date + timedelta(days=5),
        ),
    )
    session.commit()

    logger.info("Seeding ACH authorizations and transactions...")
    ach_authorization_service.create_ach_authorization(
        session, tenant.id,
        ach_authorization_service.AchAuthorizationInput(
            account_id=payroll.id, originator_id="1234567890", originator_name="Gusto Payroll",
            receiver_id=None, max_amount=Decimal("5000.00"), frequency_limit=None,
            allowed_sec_codes=["PPD"], effective_date=issue_date, expiration_date=None,
        ),
        created_by_user_id=users["approver"].id,
    )
    session.commit()
    for i in range(6):
        over_limit = i == 4
        ingest_ach_transaction(
            session, tenant.id,
            AchTransactionSubmission(
                account_id=payroll.id, originator_id="1234567890", originator_name="Gusto Payroll",
                amount=Decimal("12000.00") if over_limit else Decimal("2450.00"),
                transaction_type=AchTransactionType.DEBIT, sec_code="PPD",
                trace_number=f"00012345670000{i:02d}", effective_date=issue_date + timedelta(days=i),
            ),
        )
        session.commit()

    logger.info("Training and activating a per-customer check-network ML model (never the global model)...")
    try:
        result = train_model(session, "check", customer_id=customer.id)
        activate_model(session, result.model_row.id, expected_customer_id=customer.id)
        session.commit()
        logger.info("  Trained model %s (%d decisions).", result.model_row.id, decided_customer)
    except InsufficientTrainingData as exc:
        logger.info("  Skipping ML training (%s) -- admin page will just show no active model.", exc)
        session.rollback()

    tenant_service.update_tenant_branding(session, tenant.id, name=tenant_name, accent_color="#0f5b8a")
    session.commit()
    logger.info("Demo content seeded.")


def ensure_demo_tenant(session: Session) -> Tenant:
    """Idempotent: returns the existing demo tenant if one exists, otherwise creates and
    fully seeds one. Requires settings.demo_tenant_password to be set."""
    existing = get_demo_tenant(session)
    if existing is not None:
        return existing

    settings = get_settings()
    if not settings.demo_tenant_password:
        raise DemoTenantNotConfigured("demo_tenant_password is not configured.")

    identity = provisioning_service.create_tenant_with_admin(
        session, tenant_name=DEMO_TENANT_NAME, tenant_slug=DEMO_TENANT_SLUG,
        admin_email=DEMO_ADMIN_EMAIL, admin_password=settings.demo_tenant_password,
    )
    tenant = identity.tenant
    tenant.is_demo = True
    session.flush()
    tenant_service.set_session_timeouts(
        session, tenant.id,
        access_token_expire_minutes=settings.demo_tenant_session_minutes,
        refresh_token_expire_minutes=settings.demo_tenant_session_minutes,
    )
    session.commit()

    seed_demo_content(session, tenant, identity.admin_user, password=settings.demo_tenant_password)
    return tenant


def _purge_tenant_files(session: Session, tenant_id: uuid.UUID) -> None:
    """Deletes every on-disk file this tenant's data ever wrote -- the DB-only wipe below
    used to leave these behind forever (check images, bulk uploads, and any branding
    assets survived every reset indefinitely, and real OCR-extracted PII in a check image
    would outlive its own already-deleted DB row). Must run before the MlModel delete
    below, since an ML artifact's path can only be read off the row that's about to
    disappear.

    Two different on-disk layouts need two different strategies. check_image_storage_dir,
    bulk_upload_storage_dir, and tenant_asset_storage_dir (ocr/storage.py,
    bulk_import/file_storage.py, web/branding_storage.py) all live under
    {base_dir}/{tenant_id}/... and rmtree cleanly. ml_artifact_dir
    (ml/registry.py::ArtifactStore) is flat -- {network_code}_{customer_id}_{version}.
    joblib, no tenant_id anywhere in the path or as a column on MlModel itself
    (domain/ml_model.py) -- so each artifact has to be found via its owning Customer and
    unlinked individually."""
    for area in ("check_images", "bulk_uploads", "tenant_assets", "data_exports", "audit_archives"):
        storage.delete_tenant(area, tenant_id)

    artifact_paths = session.execute(
        select(MlModel.artifact_path).where(
            MlModel.customer_id.in_(select(Customer.id).where(Customer.tenant_id == tenant_id))
        )
    ).scalars().all()
    for artifact_path in artifact_paths:
        storage.delete(artifact_path)


def _wipe_tenant_children(session: Session, tenant_id: uuid.UUID) -> None:
    """Deletes every row belonging to tenant_id, across every tenant-scoped table --
    driven off Base.metadata (topologically sorted by every declared FK, reversed for a
    children-before-parents delete order) rather than a hand-maintained table list, so a
    table added by a future feature is automatically covered instead of silently
    surviving a reset. Also deletes the demo's own User rows (not tenant-scoped, since a
    User can belong to more than one tenant -- safe here only because these are the
    fixed, demo-only email addresses this module itself creates). Leaves the Tenant row
    itself untouched -- same id/slug/is_demo/session-timeout override survive a reset.

    The explicit SET LOCAL is required, not optional: on Postgres, the RLS session
    variable is normally only set for an already-authenticated request
    (auth/deps.py:132-133) -- a reset triggered from the pre-auth login path would
    otherwise have RLS silently filter every delete down to zero rows."""
    bind_session_to_tenant(session, tenant_id)

    _purge_tenant_files(session, tenant_id)

    # MlModel is the one exception to "every tenant-scoped table has a tenant_id column"
    # (confirmed by reading domain/ml_model.py -- it's keyed by network_code/customer_id
    # only, since the global model is deliberately shared cross-tenant). Its per-customer
    # rows still hold an FK to Customer, so they must go before the generic loop below
    # deletes this tenant's Customer rows, or that delete FK-violates.
    session.execute(
        delete(MlModel).where(MlModel.customer_id.in_(select(Customer.id).where(Customer.tenant_id == tenant_id)))
    )

    for table in reversed(Base.metadata.sorted_tables):
        if "tenant_id" in table.columns:
            session.execute(delete(table).where(table.c.tenant_id == tenant_id))
    session.execute(delete(User).where(User.email.in_(DEMO_USER_EMAILS)))
    session.flush()


# Identity and lifecycle columns a reset must never touch; every other Tenant column is a
# setting a visitor could have changed and is put back to what a freshly created demo has.
_TENANT_COLUMNS_KEPT_ON_RESET = frozenset({"id", "slug", "is_demo", "is_active", "created_at"})


def _restore_tenant_settings(tenant: Tenant, settings) -> None:
    """Puts the organization-level settings (banner and login messages, colors, dual
    control, password rules, and so on) back to a new demo's values. Wiping the tenant's
    child rows alone left these as the last visitor set them. Driven by the table's own
    column defaults, so a setting added later is reset too without touching this code."""
    for column in Tenant.__table__.columns:
        if column.key in _TENANT_COLUMNS_KEPT_ON_RESET:
            continue
        default = column.default.arg if column.default is not None and not callable(column.default.arg) else None
        setattr(tenant, column.key, default)
    tenant.name = DEMO_TENANT_NAME
    tenant.access_token_expire_minutes = settings.demo_tenant_session_minutes
    tenant.refresh_token_expire_minutes = settings.demo_tenant_session_minutes


def reset_demo_tenant(session: Session) -> Tenant:
    """Wipes and rebuilds the demo tenant from scratch -- only ever operates on the one
    tenant flagged is_demo=True, looked up fresh, never accepting a caller-supplied
    tenant. Raises DemoTenantNotConfigured rather than creating one implicitly if none
    exists yet, or if the demo password isn't configured."""
    tenant = get_demo_tenant(session)
    if tenant is None:
        raise DemoTenantNotConfigured("No demo tenant exists yet -- call ensure_demo_tenant first.")
    settings = get_settings()
    if not settings.demo_tenant_password:
        raise DemoTenantNotConfigured("demo_tenant_password is not configured.")

    logger.info("Resetting demo tenant %s...", tenant.slug)
    _wipe_tenant_children(session, tenant.id)
    _restore_tenant_settings(tenant, settings)
    session.commit()

    groups = security_group_service.seed_default_security_groups(session, tenant.id)
    ach_return_reason_service.seed_default_ach_return_reasons(session, tenant.id)
    admin_user = user_service.create_user_with_membership(
        session, tenant.id, email=DEMO_ADMIN_EMAIL, password=settings.demo_tenant_password,
        security_group_id=groups["Admin"].id,
    )
    session.commit()

    seed_demo_content(session, tenant, admin_user, password=settings.demo_tenant_password)
    return tenant


def maybe_reset_if_demo_idle_by_slug(session: Session, tenant_slug: str) -> None:
    """Called at the very start of every login attempt (web/routers/auth.py,
    api/v1/auth.py), before credentials are even checked -- a no-op for any tenant other
    than the demo one. Uses the demo admin's own last_login_at as the "has the previous
    demo session gone idle long enough to have expired" proxy: a fixed, always-present
    demo user, so this doesn't need any new per-tenant login tracking. Runs strictly
    before authenticate_password() resolves any identity for *this* request, specifically
    so a reset-triggering login can't end up minting a token against a user/membership
    row that the reset just deleted out from under it -- authenticate_password does its
    own fresh lookup afterward, which will simply find whatever exists post-reset."""
    tenant = session.execute(
        select(Tenant).where(Tenant.slug == tenant_slug, Tenant.is_demo.is_(True))
    ).scalar_one_or_none()
    if tenant is None:
        return

    admin = session.execute(select(User).where(User.email == DEMO_ADMIN_EMAIL)).scalar_one_or_none()
    if admin is None or admin.last_login_at is None:
        return

    window_minutes = tenant.refresh_token_expire_minutes or get_settings().jwt_refresh_token_expire_minutes
    # SQLite drops tzinfo on reload (same caveat ml/train.py::_seconds_since documents) --
    # a naive value here is always already-UTC by construction (server_default=func.now()).
    last_login_at = admin.last_login_at
    if last_login_at.tzinfo is None:
        last_login_at = last_login_at.replace(tzinfo=timezone.utc)
    idle_for = datetime.now(timezone.utc) - last_login_at
    if idle_for > timedelta(minutes=window_minutes):
        try:
            reset_demo_tenant(session)
        except DemoTenantNotConfigured:
            # demo_tenant_password was cleared after the tenant was created -- leave the
            # (now-stale) demo data as-is rather than raising out of the login flow.
            logger.warning("Demo tenant idle-reset skipped: demo_tenant_password is not configured.")
