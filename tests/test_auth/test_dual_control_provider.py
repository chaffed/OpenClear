# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The service provider's changes (DUAL_CONTROL_PLAN.md DC4): its own signed action log, two of
the platform's operators for what reaches every bank, and its settings watched."""

from sqlalchemy import select

from openclear.config import get_settings
from openclear.domain.platform_action_entry import PlatformActionEntry
from openclear.domain.security_event import SecurityEvent
from openclear.domain.user import User
from openclear.services import change_request_service as changes
from openclear.services import control_checks, installation_settings, platform_api_key_service, platform_log_service
from tests.conftest import TenantFactory


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


def test_a_platform_api_key_works_only_once_another_operator_approves(db_session):
    first = _operator(db_session)
    raw, key_hash = platform_api_key_service.mint()
    asked = changes.request_or_apply(db_session, "platform.api_key", {"name": "Billing", "scopes": ["usage"], "key_hash": key_hash},
                                     tenant_id=None, user_id=None, requested_with="scripts/create_metering_api_key.py")
    db_session.commit()
    assert asked.waiting and asked.change.applies_at is None
    assert raw not in str(asked.change.payload)  # only the hash waits
    assert platform_api_key_service.verify(db_session, raw) is None
    changes.decide(db_session, None, asked.change.id, approve=True, user_id=first.id)
    db_session.commit()
    assert platform_api_key_service.verify(db_session, raw).scopes == ["usage"]
    assert "platform.api_key.create" in [e.action for e in platform_log_service.entries(db_session)]


def test_the_providers_log_verification_is_kept_for_every_banks_check(db_session, tenant_factory):
    tenant, _a, _users = tenant_factory.make(slug="dcp-log")
    assert control_checks.provider_log(db_session, tenant.id).status == "WARN"  # not verified yet
    platform_log_service.record(db_session, action="platform.test", summary="One")
    platform_log_service.verify_and_keep(db_session)
    db_session.commit()
    passed = control_checks.provider_log(db_session, tenant.id)
    assert passed.status == "PASS" and passed.evidence["entries_checked"] == 1

    entry = db_session.execute(select(PlatformActionEntry)).scalar_one()
    entry.summary = "Edited"
    platform_log_service.verify_and_keep(db_session)
    db_session.commit()
    broken = control_checks.provider_log(db_session, tenant.id)
    assert broken.status == "FAIL" and broken.evidence["broken_at_sequence"] == 1
    assert db_session.execute(select(SecurityEvent).where(SecurityEvent.name == "audit.chain.verify_failed")).first()


def test_the_evidence_export_carries_the_providers_side(db_session, tenant_factory):
    from datetime import date

    from openclear.services import evidence_service

    tenant, _a, _users = tenant_factory.make(slug="dcp-evidence")
    platform_log_service.verify_and_keep(db_session)
    installation_settings.changed(db_session)  # the baseline
    db_session.commit()
    provider = evidence_service.build(db_session, tenant.id, start=date.today(), end=date.today(), generated_by_user_id=None)["service_provider"]
    assert provider["action_log"]["intact"] is True
    assert provider["settings"]["digest"] == provider["settings"]["acknowledged_digest"]
    assert provider["settings"]["changed_since_acknowledged"] == []
