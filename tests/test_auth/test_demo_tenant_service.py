# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from openclear.config import get_settings
from openclear.domain.issued_item import IssuedItem
from openclear.domain.ml_model import MlModel
from openclear.domain.tenant import Tenant
from openclear.domain.user import User
from openclear.services import account_service, demo_tenant_service, issued_item_service


def _configure_demo_password(monkeypatch, password: str = "DemoSmokeTest123!") -> None:
    monkeypatch.setattr(get_settings(), "demo_tenant_password", password)


def test_ensure_demo_tenant_creates_and_seeds(db_session, monkeypatch):
    _configure_demo_password(monkeypatch)

    tenant = demo_tenant_service.ensure_demo_tenant(db_session)

    assert tenant.is_demo is True
    assert tenant.slug == demo_tenant_service.DEMO_TENANT_SLUG
    users = db_session.query(User).filter(User.email.in_(demo_tenant_service.DEMO_USER_EMAILS)).all()
    assert len(users) == 5
    assert db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id).count() > 0

    # Safety property: never trains/activates the bare global model (customer_id=None) --
    # only a per-customer one, since MlModel has no tenant_id column at all and the
    # global model is genuinely shared across every tenant in the database.
    global_models = db_session.query(MlModel).filter(MlModel.network_code == "check", MlModel.customer_id.is_(None)).count()
    per_customer_models = db_session.query(MlModel).filter(MlModel.network_code == "check", MlModel.customer_id.is_not(None)).count()
    assert global_models == 0
    assert per_customer_models >= 1


def test_ensure_demo_tenant_is_idempotent(db_session, monkeypatch):
    _configure_demo_password(monkeypatch)

    first = demo_tenant_service.ensure_demo_tenant(db_session)
    second = demo_tenant_service.ensure_demo_tenant(db_session)

    assert first.id == second.id
    assert db_session.query(Tenant).filter(Tenant.is_demo.is_(True)).count() == 1


def test_ensure_demo_tenant_requires_password_configured(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_tenant_password", None)

    with pytest.raises(demo_tenant_service.DemoTenantNotConfigured):
        demo_tenant_service.ensure_demo_tenant(db_session)


def test_reset_demo_tenant_raises_when_none_exists(db_session, monkeypatch):
    _configure_demo_password(monkeypatch)

    with pytest.raises(demo_tenant_service.DemoTenantNotConfigured):
        demo_tenant_service.reset_demo_tenant(db_session)


def test_reset_demo_tenant_restores_pristine_state(db_session, monkeypatch):
    _configure_demo_password(monkeypatch)
    tenant = demo_tenant_service.ensure_demo_tenant(db_session)
    original_count = db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id).count()

    account = account_service.list_accounts(db_session, tenant.id)[0]
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number="SALESDEMO1", amount=Decimal("999.00"), payee_name="Test Vendor",
            issue_date=date(2026, 1, 1),
        ),
        submitted_by_user_id=None,
    )
    db_session.commit()
    assert db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id, IssuedItem.check_number == "SALESDEMO1").count() == 1

    reset_tenant = demo_tenant_service.reset_demo_tenant(db_session)

    assert reset_tenant.id == tenant.id
    db_session.expire_all()
    assert db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id, IssuedItem.check_number == "SALESDEMO1").count() == 0
    assert db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id).count() == original_count


def test_reset_demo_tenant_never_touches_a_different_tenant(db_session, monkeypatch, tenant_factory):
    _configure_demo_password(monkeypatch)
    demo_tenant_service.ensure_demo_tenant(db_session)

    real_tenant, real_account, real_users = tenant_factory.make(slug="real-bank-untouched")
    issued_item_service.create_issued_item(
        db_session, real_tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=real_account.id, check_number="REALCHECK1", amount=Decimal("500.00"), payee_name="Real Vendor",
            issue_date=date(2026, 1, 1),
        ),
        submitted_by_user_id=real_users["admin"].id,
    )
    db_session.commit()
    before = db_session.query(IssuedItem).filter(IssuedItem.tenant_id == real_tenant.id).count()

    demo_tenant_service.reset_demo_tenant(db_session)

    db_session.expire_all()
    after = db_session.query(IssuedItem).filter(IssuedItem.tenant_id == real_tenant.id).count()
    assert before == after == 1
    # And the real tenant's own admin user must still exist untouched too.
    assert db_session.get(User, real_users["admin"].id) is not None


def test_reset_demo_tenant_purges_on_disk_files(db_session, monkeypatch):
    _configure_demo_password(monkeypatch)
    tenant = demo_tenant_service.ensure_demo_tenant(db_session)
    settings = get_settings()

    check_image_dir = Path(settings.check_image_storage_dir) / str(tenant.id)
    check_image_dir.mkdir(parents=True, exist_ok=True)
    check_image_path = check_image_dir / "some_check_front.png"
    check_image_path.write_bytes(b"fake-check-image-bytes")

    bulk_upload_dir = Path(settings.bulk_upload_storage_dir) / str(tenant.id)
    bulk_upload_dir.mkdir(parents=True, exist_ok=True)
    bulk_upload_path = bulk_upload_dir / "some_upload.csv"
    bulk_upload_path.write_text("check_number,amount\n")

    tenant_asset_dir = Path(settings.tenant_asset_storage_dir) / str(tenant.id)
    tenant_asset_dir.mkdir(parents=True, exist_ok=True)
    tenant_asset_path = tenant_asset_dir / "logo.png"
    tenant_asset_path.write_bytes(b"fake-logo-bytes")

    # seed_demo_content trains a real per-customer model -- its artifact is a genuine file
    # on disk (ml/registry.py::ArtifactStore), not tenant-subdirectory-scoped like the
    # three above, so it needs its own, separately-verified cleanup path.
    old_model = db_session.query(MlModel).filter(MlModel.network_code == "check", MlModel.customer_id.is_not(None)).one()
    old_artifact_path = Path(old_model.artifact_path)
    assert old_artifact_path.exists()

    demo_tenant_service.reset_demo_tenant(db_session)

    assert not check_image_path.exists()
    assert not check_image_dir.exists()
    assert not bulk_upload_path.exists()
    assert not bulk_upload_dir.exists()
    assert not tenant_asset_path.exists()
    assert not tenant_asset_dir.exists()
    assert not old_artifact_path.exists()


def test_reset_demo_tenant_file_purge_is_safe_when_nothing_was_ever_uploaded(db_session, monkeypatch):
    # No check images/bulk uploads/branding assets exist for a first-ever reset (they're
    # only ever created by real usage, never seeded) -- the purge must not raise just
    # because a directory it looks for was never created.
    _configure_demo_password(monkeypatch)
    demo_tenant_service.ensure_demo_tenant(db_session)

    demo_tenant_service.reset_demo_tenant(db_session)  # must not raise


def test_maybe_reset_if_demo_idle_by_slug_noop_for_non_demo_tenant(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="not-the-demo-tenant")
    # Must not raise or do anything even though this tenant obviously isn't the demo one.
    demo_tenant_service.maybe_reset_if_demo_idle_by_slug(db_session, tenant.slug)


def test_maybe_reset_if_demo_idle_by_slug_noop_when_never_logged_in(db_session, monkeypatch):
    _configure_demo_password(monkeypatch)
    tenant = demo_tenant_service.ensure_demo_tenant(db_session)
    original_count = db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id).count()

    demo_tenant_service.maybe_reset_if_demo_idle_by_slug(db_session, tenant.slug)

    db_session.expire_all()
    assert db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id).count() == original_count


def test_maybe_reset_if_demo_idle_by_slug_no_reset_within_window(db_session, monkeypatch):
    _configure_demo_password(monkeypatch)
    tenant = demo_tenant_service.ensure_demo_tenant(db_session)
    admin = db_session.query(User).filter(User.email == demo_tenant_service.DEMO_ADMIN_EMAIL).one()
    admin.last_login_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    original_count = db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id).count()

    demo_tenant_service.maybe_reset_if_demo_idle_by_slug(db_session, tenant.slug)

    db_session.expire_all()
    assert db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id).count() == original_count


def test_maybe_reset_if_demo_idle_by_slug_resets_after_idle_window(db_session, monkeypatch):
    _configure_demo_password(monkeypatch)
    tenant = demo_tenant_service.ensure_demo_tenant(db_session)

    account = account_service.list_accounts(db_session, tenant.id)[0]
    issued_item_service.create_issued_item(
        db_session, tenant.id,
        issued_item_service.IssuedItemInput(
            account_id=account.id, check_number="SALESDEMO2", amount=Decimal("111.00"), payee_name="Test Vendor",
            issue_date=date(2026, 1, 1),
        ),
        submitted_by_user_id=None,
    )
    db_session.commit()

    admin = db_session.query(User).filter(User.email == demo_tenant_service.DEMO_ADMIN_EMAIL).one()
    admin.last_login_at = datetime.now(timezone.utc) - timedelta(minutes=get_settings().demo_tenant_session_minutes + 5)
    db_session.commit()

    demo_tenant_service.maybe_reset_if_demo_idle_by_slug(db_session, tenant.slug)

    db_session.expire_all()
    assert db_session.query(IssuedItem).filter(IssuedItem.tenant_id == tenant.id, IssuedItem.check_number == "SALESDEMO2").count() == 0
