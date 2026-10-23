# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Settings → Fraud scoring and Settings → ML models (SIMPLIFY_PLAN.md SP-D8, SP-D10):
web/routers/tenant_ml.py, web/routers/admin.py. Scoring is off until the bank turns it on,
turning it on is a protected `sensitive` change, and a bank sees and trains only its own
models."""

import re

import pytest
from sqlalchemy import select

from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.change_request import ChangeRequest
from openclear.domain.tenant import Tenant
from openclear.ml.registry import get_active_model_row
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


def test_page_is_for_administrators_only(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="fm-perm")
    _login(client, tenant, users["viewer"].email)
    assert client.get(PAGE, follow_redirects=False).status_code == 403


def test_scoring_starts_off_and_the_page_says_it_is_advisory(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="fm-page")
    _login(client, tenant, users["admin"].email)

    page = client.get(PAGE).text

    assert "Fraud scoring is <strong>off</strong>" in page
    assert "advisory" in page and "never decides" in page
    assert "shared model" not in page
    assert 'href="/ui/settings/fraud-model"' in client.get("/ui/settings").text


def test_turning_scoring_on_and_off(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="fm-switch")
    _login(client, tenant, users["admin"].email)

    assert "flash" in _post(client, PAGE, enabled="true").headers["location"]
    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).ml_scoring_enabled is True
    assert "Fraud scoring is <strong>on</strong>" in client.get(PAGE).text

    _post(client, PAGE, enabled="false")
    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).ml_scoring_enabled is False
    assert db_session.query(AuditLogEntry).filter_by(tenant_id=tenant.id, action="tenant.ml_scoring").count() == 2


def test_turning_scoring_on_waits_for_a_second_admin(client, db_session, tenant_factory):
    """Bringing a model into how people judge items is a `sensitive` change; turning it off
    removes one, so it applies at once."""
    from tests.test_auth.test_dual_control import _second_admin

    tenant, _account, users = tenant_factory.make(slug="fm-protected")
    _second_admin(db_session, tenant)
    db_session.get(Tenant, tenant.id).sensitive_changes_need_approval = True
    db_session.commit()
    _login(client, tenant, users["admin"].email)

    assert "approv" in _post(client, PAGE, enabled="true").headers["location"].lower()
    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).ml_scoring_enabled is False
    waiting = db_session.execute(select(ChangeRequest).where(ChangeRequest.tenant_id == tenant.id,
                                                             ChangeRequest.kind == "ml_scoring")).scalar_one()
    assert waiting.status == "pending"


def test_admin_page_while_scoring_is_off(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="fm-admin-off")
    _seed_labeled_decisions(db_session, tenant, account, users)
    _login(client, tenant, users["admin"].email)

    page = client.get("/ui/admin/ml-models").text
    retrain = _post(client, "/ui/admin/ml/retrain", network_code="check")

    assert "Fraud scoring is off" in page
    assert 'action="/ui/admin/ml/retrain"' not in page
    assert "error" in retrain.headers["location"]
    assert get_active_model_row(db_session, "check", tenant_id=tenant.id) is None


def test_retraining_from_the_admin_page(client, db_session, tenant_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_bank_model_min_decisions", 10)  # the floor itself: tests/test_ml
    tenant, account, users = tenant_factory.make(slug="fm-admin-on")
    tenant.ml_scoring_enabled = True
    db_session.commit()
    _seed_labeled_decisions(db_session, tenant, account, users)
    _login(client, tenant, users["admin"].email)

    retrain = _post(client, "/ui/admin/ml/retrain", network_code="check")

    assert "error" not in retrain.headers["location"]
    db_session.expire_all()
    model = get_active_model_row(db_session, "check", tenant_id=tenant.id)
    assert model is not None
    assert model.version in client.get("/ui/admin/ml-models").text


def test_admin_page_counts_only_this_banks_decisions(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="fm-admin-counts")
    other, other_account, other_users = tenant_factory.make(slug="fm-admin-counts-2")
    _seed_labeled_decisions(db_session, other, other_account, other_users, count=12)
    _login(client, tenant, users["admin"].email)

    page = client.get("/ui/admin/ml-models").text

    assert re.search(r"<td[^>]*>check</td><td[^>]*>0</td>", page)
