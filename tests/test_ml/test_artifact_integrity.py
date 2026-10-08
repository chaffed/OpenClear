# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Model artifacts are pickles, so ml/registry.py only unpickles a file whose SHA-256
matches the one recorded on its ml_model row when it was written."""

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select

from openclear.config import get_settings
from openclear.domain.exception_item import ExceptionItem
from openclear.ml import predict, registry
from openclear.ml.predict import score_exception
from openclear.ml.registry import ArtifactIntegrityError, ArtifactStore, get_active_model_row
from openclear.ml.train import train_model
from openclear.services import tenant_ml_service
from tests.test_ml.test_bank_model_choice import _bank, _row


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_retrain_cooldown_seconds", 0)
    predict.reset_model_cache()
    yield
    predict.reset_model_cache()


def _trained_shared_model(db_session, tenant_factory, slug):
    tenant, _account, users = _bank(tenant_factory, db_session, slug, decisions=12)
    row = train_model(db_session, "check").model_row
    db_session.commit()
    return tenant, users, row


def _tamper(row):
    path = Path(row.artifact_path)
    path.write_bytes(path.read_bytes() + b"\x00")


def _no_unpickling(monkeypatch):
    calls = []
    monkeypatch.setattr(registry.joblib, "load", lambda *a, **k: calls.append(a))
    return calls


def test_training_records_the_sha256_of_the_file_it_wrote(db_session, tenant_factory):
    _tenant, _users, row = _trained_shared_model(db_session, tenant_factory, "sha-rec")

    assert row.artifact_sha256 == hashlib.sha256(Path(row.artifact_path).read_bytes()).hexdigest()
    assert ArtifactStore().load_model(row) is not None


def test_a_changed_file_is_refused_without_being_unpickled(db_session, tenant_factory, monkeypatch):
    _tenant, _users, row = _trained_shared_model(db_session, tenant_factory, "sha-chg")
    _tamper(row)
    calls = _no_unpickling(monkeypatch)

    with pytest.raises(ArtifactIntegrityError, match="has changed"):
        ArtifactStore().load_model(row)
    assert calls == []


def test_a_row_with_no_recorded_fingerprint_is_refused(db_session, monkeypatch):
    row = _row(db_session)
    calls = _no_unpickling(monkeypatch)

    with pytest.raises(ArtifactIntegrityError, match="No fingerprint"):
        ArtifactStore().load_model(row)
    assert calls == []


def test_scoring_skips_a_tampered_model_instead_of_failing(db_session, tenant_factory, caplog):
    tenant, _users, row = _trained_shared_model(db_session, tenant_factory, "sha-scr")
    exception_item = db_session.execute(select(ExceptionItem).where(ExceptionItem.tenant_id == tenant.id)).scalars().first()
    exception_item.ml_score = None
    _tamper(row)

    assert score_exception(db_session, exception_item) is None
    assert exception_item.ml_score is None
    assert "failed the integrity check" in caplog.text


def test_scoring_skips_a_missing_model_file_instead_of_failing(db_session, tenant_factory, caplog):
    tenant, _users, row = _trained_shared_model(db_session, tenant_factory, "sha-missing")
    exception_item = db_session.execute(select(ExceptionItem).where(ExceptionItem.tenant_id == tenant.id)).scalars().first()
    exception_item.ml_score = None
    Path(row.artifact_path).unlink()

    assert score_exception(db_session, exception_item) is None
    assert "can't be read" in caplog.text


def test_switching_to_bank_only_never_copies_a_tampered_shared_model(db_session, tenant_factory):
    tenant, users, shared = _trained_shared_model(db_session, tenant_factory, "sha-swt")
    _tamper(shared)

    seeded = tenant_ml_service.switch_to_bank_only(db_session, tenant.id, actor_user_id=users["admin"].id)
    db_session.commit()

    assert seeded == []
    assert get_active_model_row(db_session, "check", tenant_id=tenant.id) is None
