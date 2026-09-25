# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → Fraud scoring model and the Admin page's model section (FIX_PLAN.md
Phase 5.6) — web/routers/tenant_ml.py, web/routers/admin.py."""

from datetime import datetime, timedelta, timezone

import pytest

from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.tenant import MlModelSource
from openclear.ml.registry import get_active_model_row
from openclear.ml.train import train_model
from openclear.services import wizard_service
from tests.conftest import TenantFactory
from tests.test_api.test_admin_ml import _seed_labeled_decisions

PAGE = "/ui/settings/fraud-model"


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_retrain_cooldown_seconds", 0)


def _login(client, tenant, email):
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token")})


def _post(client, path, **data):
    return client.post(path, data={"csrf_token": client.cookies.get("csrf_token"), **data}, follow_redirects=False)


def test_page_is_for_organization_managers_only(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="fm-perm")
    _login(client, tenant, users["viewer"].email)
    assert client.get(PAGE, follow_redirects=False).status_code == 403


def test_page_explains_the_choice_and_flags_placeholder_disclosure(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="fm-page")
    _login(client, tenant, users["admin"].email)

    page = client.get(PAGE).text

    assert "the shared model" in page
    assert "placeholder wording" in page
    assert "Switch to a bank-only model" in page
    assert 'href="/ui/settings/fraud-model"' in client.get("/ui/settings").text


def test_acknowledging_the_disclosure_is_recorded_and_completes_the_setup_step(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="fm-consent")
    _login(client, tenant, users["admin"].email)

    assert "error" in _post(client, f"{PAGE}/consent").headers["location"]  # the box must be ticked
    resp = _post(client, f"{PAGE}/consent", consent="true")

    assert "flash" in resp.headers["location"]
    db_session.expire_all()
    assert tenant.ml_shared_consent_by_user_id == users["admin"].id
    assert db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id, action="tenant.ml_shared_consent").count() == 1
    step = next(v for v in wizard_service.get_bank_wizard_steps(db_session, tenant.id) if v.step.key == "fraud_model")
    assert step.is_complete


def test_switching_to_bank_only_and_back(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fm-switch")
    _seed_labeled_decisions(db_session, tenant, account, users)
    train_model(db_session, "check")
    _login(client, tenant, users["admin"].email)

    assert "error" in _post(client, f"{PAGE}/switch-to-bank-only").headers["location"]  # must confirm
    _post(client, f"{PAGE}/switch-to-bank-only", confirm="true")

    db_session.expire_all()
    assert tenant.ml_model_source == MlModelSource.PRIVATE
    assert get_active_model_row(db_session, "check", tenant_id=tenant.id) is not None
    admin_page = client.get("/ui/admin").text
    assert "Your bank-only model" in admin_page and "seed-from-shared-" in admin_page

    assert "error" in _post(client, f"{PAGE}/switch-to-shared").headers["location"]  # must consent
    _post(client, f"{PAGE}/switch-to-shared", consent="true")

    db_session.expire_all()
    assert tenant.ml_model_source == MlModelSource.SHARED
    assert db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id, action="tenant.ml_source_change").count() == 2


def test_a_new_bank_sees_when_it_can_switch(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="fm-locked")
    allowed_at = datetime.now(timezone.utc) + timedelta(days=30)
    tenant.ml_private_switch_allowed_at = allowed_at
    db_session.commit()
    _login(client, tenant, users["admin"].email)

    page = client.get(PAGE).text
    resp = _post(client, f"{PAGE}/switch-to-bank-only", confirm="true")

    assert f"<strong>{allowed_at:%Y-%m-%d}</strong>" in page
    assert "first+90+days" in resp.headers["location"] or "first%2090%20days" in resp.headers["location"]
    db_session.expire_all()
    assert tenant.ml_model_source == MlModelSource.SHARED


def test_admin_page_for_a_shared_bank_is_read_only(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fm-admin-shared")
    _seed_labeled_decisions(db_session, tenant, account, users)
    train_model(db_session, "check")
    _login(client, tenant, users["admin"].email)

    page = client.get("/ui/admin").text
    retrain = _post(client, "/ui/admin/ml/retrain", network_code="check")

    assert "Shared model" in page and "Banks contributing" in page
    assert 'action="/ui/admin/ml/retrain"' not in page
    assert "error" in retrain.headers["location"] and "platform+operator" in retrain.headers["location"].replace("%20", "+")


def test_admin_page_counts_only_this_banks_decisions(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fm-admin-counts")
    other, other_account, other_users = tenant_factory.make(slug="fm-admin-counts-2")
    _seed_labeled_decisions(db_session, other, other_account, other_users, count=12)
    _login(client, tenant, users["admin"].email)

    page = client.get("/ui/admin").text

    assert "<td>check</td><td>0</td><td>0</td>" in page
