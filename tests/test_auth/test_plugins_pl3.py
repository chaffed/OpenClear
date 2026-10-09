# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Plugins PL3 (PLUGIN_PLAN.md): matching rules, scoring features and notification
channels, with the example plugin and with deliberately broken ones."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from openclear import plugins
from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.tenant_extension import TenantExtension
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.plugins.api import MatchingRule, ScoringFeature
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import issued_item_service, plugin_service
from openclear.workers import jobs
from tests.conftest import TenantFactory
from tests.test_auth.test_plugins import _FakeEntryPoint, _plugin, installed  # noqa: F401 -- fixture
from tests.test_auth.test_plugins_bank_choices import example_plugin  # noqa: F401 -- fixture


def _present(db_session, tenant, account, users, number, issued, presented):
    """Issues a check and presents it; returns the exception's types, or None if it matched."""
    issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number=number, amount=Decimal(issued), payee_name="Vendor", issue_date=date(2026, 1, 1),
    ), submitted_by_user_id=users["preparer"].id)
    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(
        account_id=account.id, check_number=number, presented_amount=Decimal(presented), presented_date=date(2026, 1, 10),
    ))
    db_session.commit()
    found = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid.id)
    return found[0] if found else None


def _turn_on(db_session, tenant, point, name, config=None):
    plugin_service.set_enabled(db_session, tenant.id, point, name, True, user_id=None, config=config)
    db_session.commit()


def test_a_rule_adds_exceptions_only_for_banks_that_turned_it_on(example_plugin, db_session, tenant_factory):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="pl3-rule")
    assert _present(db_session, tenant, account, users, "R1", "20000.00", "20000.00") is None  # off: matches

    _turn_on(db_session, tenant, "matching_rules", "example_large_round_amount")
    flagged = _present(db_session, tenant, account, users, "R2", "20000.00", "20000.00")
    assert flagged.exception_types == "large_round_amount"
    assert _present(db_session, tenant, account, users, "R3", "20000.50", "20000.50") is None  # not round

    both = _present(db_session, tenant, account, users, "R4", "1000.00", "30000.00")  # the built-in rule's finding stays
    assert set(both.exception_types.split(",")) == {"amount_mismatch", "large_round_amount"}

    from openclear.web.templates import _exception_type_label

    assert _exception_type_label("large_round_amount") == "Large round-amount check"


def test_a_broken_rule_fails_closed(installed, db_session, tenant_factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "plugins", "broken_rules")

    def explode(session, item):
        raise RuntimeError("watch list unreachable")

    def register(registry):
        registry.add_matching_rule(MatchingRule("explodes", "check", {"watch_hit": "On the watch list"}, explode))
        registry.add_matching_rule(MatchingRule("undeclared", "check", {"other_hit": "Other"}, lambda s, i: ["not_declared"]))

    installed(_FakeEntryPoint("broken_rules", _plugin("broken_rules", register)))
    tenant, account, users = tenant_factory.make(slug="pl3-broken")
    _turn_on(db_session, tenant, "matching_rules", "explodes")
    assert _present(db_session, tenant, account, users, "B1", "5.00", "5.00").exception_types == "plugin_rule_failed"

    plugin_service.set_enabled(db_session, tenant.id, "matching_rules", "explodes", False, user_id=None)
    _turn_on(db_session, tenant, "matching_rules", "undeclared")
    assert _present(db_session, tenant, account, users, "B2", "5.00", "5.00").exception_types == "plugin_rule_failed"


def test_a_rule_cant_claim_a_built_in_exception_type(installed, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "plugins", "squatter")
    register = lambda r: r.add_matching_rule(MatchingRule("squat", "check", {"stopped": "Mine now"}, lambda s, i: []))  # noqa: E731
    status = {p.name: p for p in installed(_FakeEntryPoint("squatter", _plugin("squatter", register)))}
    assert status["squatter"].status == "failed" and "stopped" in status["squatter"].error
    assert "squat" not in plugins.registry.matching_rules


def test_scoring_features_reach_the_model_and_a_broken_one_is_skipped(installed, db_session, tenant_factory, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "plugins", "features")

    def register(registry):
        registry.add_scoring_feature(ScoringFeature("good", "check", lambda s, e: {"score": 0.25, "flag": True, "skip": None}))
        registry.add_scoring_feature(ScoringFeature("bad", "check", lambda s, e: 1 / 0))

    installed(_FakeEntryPoint("features", _plugin("features", register)))
    tenant, account, users = tenant_factory.make(slug="pl3-features")
    exception = _present(db_session, tenant, account, users, "S1", "5.00", "6.00")

    from openclear.networks.registry import get_adapter

    features = get_adapter("check").build_features(db_session, exception)
    assert features["plugin:good:score"] == 0.25 and features["plugin:good:flag"] == 1
    assert "plugin:good:skip" not in features and not any(k.startswith("plugin:bad:") for k in features)
    assert "amount_difference" in features or len(features) > 3  # the network's own features are still there


def test_the_example_feature(example_plugin, db_session, tenant_factory):  # noqa: F811
    tenant, account, users = tenant_factory.make(slug="pl3-example-feature")
    exception = _present(db_session, tenant, account, users, "S2", "1.00", "3000.00")
    from openclear.networks.registry import get_adapter

    features = get_adapter("check").build_features(db_session, exception)
    assert features["plugin:example_round_amount:round_thousands"] == 1


def test_a_notification_channel_gets_the_banks_events_with_its_settings(example_plugin, client, db_session, tenant_factory):  # noqa: F811
    import openclear_plugin_example

    openclear_plugin_example.SENT.clear()
    tenant, account, users = tenant_factory.make(slug="pl3-channel")
    other, other_account, other_users = tenant_factory.make(slug="pl3-channel-other")
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    page = client.get("/ui/admin/plugins")
    assert 'name="config_prefix"' in page.text and 'name="config_token"' in page.text

    def choose(**fields):
        return client.post("/ui/admin/plugins/choices", follow_redirects=False, data={
            "csrf_token": csrf, "point": "notification_channels", "name": "example_log", "enabled": "true", **fields})

    missing = choose(config_prefix="", config_token="")
    assert "Message+prefix+is+required" in missing.headers["location"]
    assert choose(config_prefix="[Acme]", config_token="tok-123").status_code == 303
    row = db_session.execute(select(TenantExtension).where(TenantExtension.tenant_id == tenant.id)).scalar_one()
    assert "tok-123" not in row.config_encrypted
    assert "tok-123" not in client.get("/ui/admin/plugins").text  # a secret isn't shown again
    choose(config_prefix="[Acme Bank]", config_token="")  # blank keeps the saved secret

    _present(db_session, tenant, account, users, "N1", "5.00", "9.00")
    _present(db_session, other, other_account, other_users, "N2", "5.00", "9.00")  # hasn't turned it on
    jobs.drain(db_session.get_bind())
    [(event, config)] = openclear_plugin_example.SENT
    assert event["type"] == "exception.created" and event["bank"] == tenant.name and "/ui/exceptions/" in event["link"]
    assert config == {"prefix": "[Acme Bank]", "token": "tok-123"}
    audit = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id, AuditLogEntry.action == "plugin.enable")).scalars().all()
    assert len(audit) == 2


def test_a_failing_channel_is_retried_not_lost(installed, db_session, tenant_factory, monkeypatch):  # noqa: F811
    from openclear.domain.job import Job, JobStatus
    from openclear.plugins.api import NotificationChannel

    monkeypatch.setattr(get_settings(), "plugins", "flaky")
    calls = []

    def send(event, config):
        calls.append(event["type"])
        raise ConnectionError("chat service down")

    channel = NotificationChannel("flaky", "Flaky", send)
    installed(_FakeEntryPoint("flaky", _plugin("flaky", lambda r: r.add_notification_channel(channel))))
    tenant, account, users = tenant_factory.make(slug="pl3-flaky")
    _turn_on(db_session, tenant, "notification_channels", "flaky")
    _present(db_session, tenant, account, users, "F1", "5.00", "9.00")
    jobs.drain(db_session.get_bind())
    assert calls == ["exception.created"]
    job = db_session.execute(select(Job).where(Job.kind == "plugin.notify")).scalar_one()
    db_session.refresh(job)
    assert job.status == JobStatus.QUEUED and "chat service down" in job.last_error  # waiting to retry


def test_plugin_examples_cover_every_bank_choice(example_plugin):  # noqa: F811
    points = {c["point"] for c in plugins.bank_choices()}
    assert points == set(plugins.BANK_CHOICE_POINTS)
