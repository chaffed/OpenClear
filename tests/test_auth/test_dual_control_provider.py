# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The service provider's changes (DUAL_CONTROL_PLAN.md DC4): its own signed action log, two of
the platform's operators for what reaches every bank, and its settings watched."""

from sqlalchemy import select

from openclear.config import get_settings
from openclear.domain.ml_model import MlModel, MlModelStatus
from openclear.domain.platform_action_entry import PlatformActionEntry
from openclear.domain.security_event import SecurityEvent
from openclear.domain.user import User
from openclear.ml.registry import create_model_row
from openclear.services import change_request_service as changes
from openclear.services import control_checks, installation_settings, platform_api_key_service, platform_log_service
from tests.conftest import TenantFactory, approve_provider_changes


def _operator(db_session, email="ops@platform.example"):
    from openclear.auth.security import hash_password

    user = User(email=email, hashed_password=hash_password(TenantFactory.PASSWORD), platform_role="operator")
    db_session.add(user)
    db_session.commit()
    return user


def test_the_providers_log_is_chained_and_signed(db_session):
    platform_log_service.record(db_session, action="platform.test", summary="First")
    platform_log_service.record(db_session, action="platform.test", summary="Second")
    db_session.commit()
    intact, checked, broken = platform_log_service.verify(db_session)
    assert (intact, checked, broken) == (True, 2, None)
    assert db_session.execute(select(SecurityEvent).where(SecurityEvent.name == "platform.action")).first()
    first = db_session.execute(select(PlatformActionEntry).where(PlatformActionEntry.sequence == 1)).scalar_one()
    first.summary = "Edited"
    db_session.commit()
    assert platform_log_service.verify(db_session)[0] is False


def test_activating_the_shared_model_waits_for_an_operator(client, db_session):
    model = create_model_row(db_session, network_code="check", version="v-shared", algorithm="logistic_regression", artifact_path="/tmp/x.joblib",
                             trained_from_decision_count=10, metrics_json={}, status=MlModelStatus.RETIRED)
    db_session.commit()
    _row, raw = platform_api_key_service.generate_and_create(db_session, "Pipeline", ("shared_model",))
    db_session.commit()
    asked = client.patch(f"/api/v1/platform/ml/models/{model.id}/activate", headers={"X-Api-Key": raw})
    assert asked.status_code == 202 and asked.json()["status"] == "pending"
    db_session.expire_all()
    assert db_session.get(MlModel, model.id).status == MlModelStatus.RETIRED
    [change] = changes.pending(db_session, None)
    assert change.scope == "provider" and change.requested_by_user_id is None and change.applies_at is not None  # no operator yet
    approve_provider_changes(db_session)
    assert db_session.get(MlModel, model.id).status == MlModelStatus.ACTIVE
    actions = [e.action for e in platform_log_service.entries(db_session)]
    assert {"change_request.create", "change_request.approve", "platform.shared_model.activate"} <= set(actions)
    created = db_session.execute(select(PlatformActionEntry).where(PlatformActionEntry.action == "change_request.create")).scalar_one()
    assert created.actor_key_id is not None  # asked for with the key, not a person


def test_a_platform_role_waits_for_another_operator(db_session):
    first = _operator(db_session)
    newcomer = User(email="new.ops@platform.example", hashed_password="x")
    db_session.add(newcomer)
    db_session.commit()
    asked = changes.request_or_apply(db_session, "platform.role", {"email": newcomer.email, "role": "operator"}, tenant_id=None,
                                     user_id=None, requested_with="scripts/platform_staff.py")
    db_session.commit()
    assert asked.waiting and asked.change.applies_at is None  # an operator can approve
    changes.decide(db_session, None, asked.change.id, approve=True, user_id=first.id)
    db_session.commit()
    assert db_session.get(User, newcomer.id).platform_role == "operator"
    removed = changes.request_or_apply(db_session, "platform.role", {"email": newcomer.email, "role": None}, tenant_id=None, user_id=first.id)
    assert removed.applied  # removing never waits


def test_installation_settings_are_watched_until_acknowledged(db_session, tenant_factory, monkeypatch):
    tenant, _a, _users = tenant_factory.make(slug="dcp-settings")
    operator = _operator(db_session)
    assert control_checks.installation_settings(db_session, tenant.id).status == "PASS"  # the first look is the baseline
    db_session.commit()
    monkeypatch.setattr(get_settings(), "release_feed_url", "https://mirror.example/releases")
    changed = control_checks.installation_settings(db_session, tenant.id)
    assert changed.status == "WARN" and changed.evidence["changed"] == ["release_feed_url"]
    assert "mirror.example" not in str(changed.evidence)  # digests, never values
    assert installation_settings.acknowledge(db_session, user_id=operator.id) == ["release_feed_url"]
    db_session.commit()
    assert control_checks.installation_settings(db_session, tenant.id).status == "PASS"
    assert [e.action for e in platform_log_service.entries(db_session)][0] == "platform.settings.acknowledged"
