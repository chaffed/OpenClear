# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Switching between the shared model and a bank-only model (FIX_PLAN.md Phase 5.3) —
services/tenant_ml_service.py."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openclear.config import get_settings
from openclear.domain.tenant import MlModelSource, Tenant
from openclear.ml.predict import _resolve_scoring_source
from openclear.ml.registry import ArtifactStore, get_active_model_row
from openclear.ml.train import train_model
from openclear.services import provisioning_service, tenant_ml_service
from tests.test_ml.test_bank_model_choice import _bank


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_retrain_cooldown_seconds", 0)


def _switch(db_session, tenant, user):
    seeded = tenant_ml_service.switch_to_bank_only(db_session, tenant.id, actor_user_id=user.id)
    db_session.commit()
    return seeded


# --- The 90-day lock ---


def test_a_new_bank_cannot_switch_during_its_first_90_days(db_session):
    identity = provisioning_service.create_tenant_with_admin(
        db_session, tenant_name="Brand New Bank", tenant_slug="brand-new", admin_email="a@new.example.com",
        admin_password="Long-enough-pw-1",
    )
    db_session.commit()
    allowed_at = identity.tenant.ml_private_switch_allowed_at
    assert allowed_at is not None
    assert 89 <= (allowed_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days <= 90

    with pytest.raises(tenant_ml_service.SwitchNotAllowed, match="first 90 days"):
        tenant_ml_service.switch_to_bank_only(db_session, identity.tenant.id, actor_user_id=identity.admin_user.id)


def test_the_lock_ends_after_90_days(db_session):
    identity = provisioning_service.create_tenant_with_admin(
        db_session, tenant_name="Older Bank", tenant_slug="older-new", admin_email="a@older.example.com",
        admin_password="Long-enough-pw-1",
    )
    identity.tenant.ml_private_switch_allowed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    _switch(db_session, identity.tenant, identity.admin_user)

    assert db_session.get(Tenant, identity.tenant.id).ml_model_source == MlModelSource.PRIVATE


def test_banks_that_predate_the_lock_are_never_locked(db_session, tenant_factory):
    tenant, _account, users = _bank(tenant_factory, db_session, "pre-lock", decisions=0)
    assert tenant.ml_private_switch_allowed_at is None

    _switch(db_session, tenant, users["admin"])

    assert tenant.ml_model_source == MlModelSource.PRIVATE


def test_platform_operator_can_lift_the_lock(db_session):
    identity = provisioning_service.create_tenant_with_admin(
        db_session, tenant_name="Rush Bank", tenant_slug="rush-new", admin_email="a@rush.example.com",
        admin_password="Long-enough-pw-1",
    )
    db_session.commit()

    tenant_ml_service.clear_switch_lock(db_session, identity.tenant.id)
    _switch(db_session, identity.tenant, identity.admin_user)

    assert identity.tenant.ml_model_source == MlModelSource.PRIVATE


# --- Seeding the bank model from the shared one ---


def test_switching_copies_the_shared_model_so_scoring_never_has_a_gap(db_session, tenant_factory):
    bank, _account, users = _bank(tenant_factory, db_session, "seed-bnk", decisions=12)
    shared = train_model(db_session, "check").model_row

    seeded = _switch(db_session, bank, users["admin"])

    assert len(seeded) == 1
    bank_model = get_active_model_row(db_session, "check", tenant_id=bank.id)
    assert bank_model.id == seeded[0].id
    assert bank_model.version.startswith("seed-from-shared-")
    assert bank_model.metrics_json["evaluation"]["seeded_from_model_id"] == str(shared.id)
    assert bank_model.artifact_path != shared.artifact_path and Path(bank_model.artifact_path).exists()
    assert _resolve_scoring_source(db_session, bank.id, "check", None).id == bank_model.id
    # The shared model itself is untouched and still active for everyone else.
    assert get_active_model_row(db_session, "check").id == shared.id


def test_the_seeded_copy_scores_exactly_like_the_shared_model(db_session, tenant_factory):
    bank, _account, users = _bank(tenant_factory, db_session, "seed-eq", decisions=12)
    shared = train_model(db_session, "check").model_row
    seeded = _switch(db_session, bank, users["admin"])[0]
    store = ArtifactStore()
    features = [{"amount_mismatch": 1, "presented_amount": 999.0}, {"amount_mismatch": 0, "presented_amount": 100.0}]

    assert list(store.load_model(seeded).predict_proba(features)) == list(store.load_model(shared).predict_proba(features))
    assert seeded.artifact_sha256 == shared.artifact_sha256


def test_switching_with_no_shared_model_leaves_the_bank_unscored_until_it_trains(db_session, tenant_factory):
    bank, _account, users = _bank(tenant_factory, db_session, "seed-none", decisions=0)

    assert _switch(db_session, bank, users["admin"]) == []
    assert _resolve_scoring_source(db_session, bank.id, "check", None) is None


def test_switching_records_who_and_when(db_session, tenant_factory):
    bank, _account, users = _bank(tenant_factory, db_session, "seed-who", decisions=0)

    _switch(db_session, bank, users["admin"])

    assert bank.ml_source_changed_by_user_id == users["admin"].id
    assert bank.ml_source_changed_at is not None


# --- Switching back ---


def test_switching_back_to_shared_needs_consent_and_records_it(db_session, tenant_factory):
    bank, _account, users = _bank(tenant_factory, db_session, "back-shr", decisions=12)
    shared = train_model(db_session, "check").model_row
    _switch(db_session, bank, users["admin"])

    with pytest.raises(tenant_ml_service.SwitchNotAllowed, match="confirm"):
        tenant_ml_service.switch_to_shared(db_session, bank.id, actor_user_id=users["admin"].id, consented=False)

    tenant_ml_service.switch_to_shared(db_session, bank.id, actor_user_id=users["admin"].id, consented=True)
    db_session.commit()

    assert bank.ml_model_source == MlModelSource.SHARED
    assert bank.ml_shared_consent_by_user_id == users["admin"].id
    assert _resolve_scoring_source(db_session, bank.id, "check", None).id == shared.id
    # The bank-only model stays in history, ready if the bank switches again.
    assert get_active_model_row(db_session, "check", tenant_id=bank.id) is not None


def test_already_on_the_requested_model_is_rejected(db_session, tenant_factory):
    bank, _account, users = _bank(tenant_factory, db_session, "same-src", decisions=0)
    with pytest.raises(tenant_ml_service.SwitchNotAllowed, match="already uses the shared"):
        tenant_ml_service.switch_to_shared(db_session, bank.id, actor_user_id=users["admin"].id, consented=True)


# --- The shared model forgets a bank that leaves ---


def test_scheduled_retrain_replaces_the_shared_model_after_a_bank_leaves(db_session, tenant_factory, session_factory, monkeypatch):
    from openclear.workers import tasks

    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(get_settings(), "ml_min_new_decisions_for_retrain", 10_000)  # only the "bank left" rule can fire
    stays, _a, _u = _bank(tenant_factory, db_session, "left-stay", decisions=12)
    leaves, _b, leaver_users = _bank(tenant_factory, db_session, "left-goes", decisions=12)
    before = train_model(db_session, "check").model_row
    assert before.trained_from_decision_count == 24

    _switch(db_session, leaves, leaver_users["admin"])
    tasks.retrain_job()

    db_session.expire_all()
    after = get_active_model_row(db_session, "check")
    assert after.id != before.id
    assert after.trained_from_decision_count == 12
    assert "still contained its data" in after.metrics_json["evaluation"]["reason"]

    # And it doesn't keep retraining once the departed bank's data is gone.
    tasks.retrain_job()
    db_session.expire_all()
    assert get_active_model_row(db_session, "check").id == after.id


def test_the_shared_summary_shows_counts_only(db_session, tenant_factory):
    _bank(tenant_factory, db_session, "sum-aaa", decisions=12)
    _bank(tenant_factory, db_session, "sum-bbb", decisions=12)
    train_model(db_session, "check")

    check = next(s for s in tenant_ml_service.shared_model_summaries(db_session) if s.network_code == "check")

    assert check.contributing_bank_count == 2
    assert check.trained_from_decision_count == 24
    # Nothing that could identify another bank: no ids, just a version, time, and counts.
    from dataclasses import fields

    assert {f.name for f in fields(check)} == {
        "network_code", "version", "activated_at", "trained_from_decision_count", "contributing_bank_count",
    }
