# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Control test results, deviations and the checks CT2 adds (CONTROLS_PLAN.md CT2;
services/control_results_service.py, services/control_checks.py)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from openclear.compliance import checks
from openclear.domain.control_result import ControlDeviation, ControlTestResult
from openclear.domain.security_event import SecurityEvent
from openclear.services import control_checks, control_results_service, security_group_service, user_service
from openclear.services.control_results_service import Outcome
from tests.conftest import TenantFactory


def _record(db_session, tenant, key, status, *, started_by="schedule", only_changes=False):
    kept = control_results_service.record(db_session, tenant.id, [Outcome(key, status, f"{key} is {status}", {"n": 1})],
                                          started_by=started_by, only_changes=only_changes)
    db_session.commit()
    return kept


def _deviations(db_session, tenant):
    db_session.expire_all()
    return db_session.execute(select(ControlDeviation).where(ControlDeviation.tenant_id == tenant.id)
                              .order_by(ControlDeviation.opened_at)).scalars().all()


def _login(client, tenant, email):
    client.cookies.clear()
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    return client.cookies.get("csrf_token")


# --- Results and deviations --------------------------------------------------------------------


def test_a_fail_opens_a_deviation_and_a_pass_closes_it(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="dev-open")
    [first] = _record(db_session, tenant, "sla", "PASS")
    assert first.evidence_sha256 == control_results_service.digest({"n": 1}) and _deviations(db_session, tenant) == []
    [failed] = _record(db_session, tenant, "sla", "FAIL")
    _record(db_session, tenant, "sla", "FAIL")  # still failing: the same deviation
    [deviation] = _deviations(db_session, tenant)
    assert deviation.opened_result_id == failed.id and deviation.closed_at is None and deviation.status == "FAIL"
    [recovered] = _record(db_session, tenant, "sla", "PASS")
    [deviation] = _deviations(db_session, tenant)
    assert deviation.closed_result_id == recovered.id and deviation.closed_at is not None
    assert db_session.execute(select(ControlTestResult).where(ControlTestResult.tenant_id == tenant.id)).scalars().all().__len__() == 4


def test_warnings_count_only_for_the_checks_the_bank_chooses(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="dev-warn")
    _record(db_session, tenant, "staff_mfa", "WARN")
    assert _deviations(db_session, tenant) == []
    tenant.control_warn_deviations = ["staff_mfa"]
    db_session.commit()
    _record(db_session, tenant, "staff_mfa", "PASS")
    _record(db_session, tenant, "staff_mfa", "WARN")
    _record(db_session, tenant, "staff_mfa", "FAIL")  # worse while open
    [deviation] = _deviations(db_session, tenant)
    assert deviation.status == "FAIL" and deviation.closed_at is None


def test_a_page_view_keeps_only_changes(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="dev-view")
    assert len(_record(db_session, tenant, "sla", "PASS", started_by="view", only_changes=True)) == 1
    assert _record(db_session, tenant, "sla", "PASS", started_by="view", only_changes=True) == []
    assert len(_record(db_session, tenant, "sla", "WARN", started_by="view", only_changes=True)) == 1


def test_viewing_the_controls_records_state_changes_not_population(client, db_session, tenant_factory):
    from openclear.services import controls_service

    tenant, _account, _users = tenant_factory.make(slug="dev-page")
    controls_service.status(db_session, tenant.id)
    db_session.expire_all()
    recorded = {r.check_key for r in db_session.execute(select(ControlTestResult).where(ControlTestResult.tenant_id == tenant.id)).scalars()}
    population = {c.key for c in checks.registry() if c.kind == "population"}
    assert recorded and not recorded & population and "sla" in recorded
    controls_service.status(db_session, tenant.id)  # nothing changed: nothing more kept
    db_session.expire_all()
    assert len(db_session.execute(select(ControlTestResult).where(ControlTestResult.tenant_id == tenant.id)).scalars().all()) == len(recorded)


def test_the_hourly_and_nightly_runners(db_session, session_factory, tenant_factory, monkeypatch):
    from openclear.db import session as db_session_module
    from openclear.workers import tasks

    tenant, _account, _users = tenant_factory.make(slug="dev-runner")
    monkeypatch.setattr(db_session_module, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(tasks, "get_session_factory", lambda: session_factory)
    tasks.controls_watch_job()
    tasks.controls_watch_job()
    db_session.expire_all()
    rows = db_session.execute(select(ControlTestResult).where(ControlTestResult.tenant_id == tenant.id)).scalars().all()
    hourly = {c.key for c in checks.registry() if c.schedule == "hourly" and not c.needs_request}
    assert {r.check_key for r in rows} == hourly and "edge" not in {r.check_key for r in rows}
    assert len(rows) == 2 * len(hourly) and {r.started_by for r in rows} == {"schedule"}  # every scheduled run kept

    tasks.controls_nightly_job()
    db_session.expire_all()
    night = db_session.execute(select(ControlTestResult).where(ControlTestResult.tenant_id == tenant.id,
                                                               ControlTestResult.check_key == "dual_control")).scalar_one()
    start, end = control_results_service.previous_day()
    assert night.period_start.replace(tzinfo=timezone.utc) == start and night.period_end.replace(tzinfo=timezone.utc) == end


def test_retention_keeps_an_open_deviations_result(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="dev-prune")
    [old_fail] = _record(db_session, tenant, "sla", "FAIL")
    [old_pass] = _record(db_session, tenant, "sla", "PASS")
    [new_fail] = _record(db_session, tenant, "scim_sync", "FAIL")
    long_ago = datetime.now(timezone.utc) - timedelta(days=4000)
    for result in (old_fail, old_pass, new_fail):
        result.recorded_at = long_ago
    db_session.commit()  # before _deviations, which expires unsaved changes
    _deviations(db_session, tenant)[0].closed_at = long_ago
    db_session.commit()
    results, deviations = control_results_service.prune(db_session, older_than=datetime.now(timezone.utc) - timedelta(days=2555))
    db_session.commit()
    assert deviations == 1
    kept = {r.id for r in db_session.execute(select(ControlTestResult).where(ControlTestResult.tenant_id == tenant.id)).scalars()}
    assert kept == {new_fail.id}  # the open scim_sync deviation keeps the result that opened it


# --- Checks ------------------------------------------------------------------------------------


def test_dual_control_over_a_period(db_session, tenant_factory):
    from openclear.domain.decision import Decision, DecisionOutcome, DecisionSource
    from tests.items import _make_exception

    tenant, account, users = tenant_factory.make(slug="chk-dual", require_dual_control=True)
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    for number, maker in (("5001", users["preparer"]), ("5002", users["approver"])):
        item = _make_exception(db_session, tenant, account, users, number, "10.00", "20.00")
        db_session.add(Decision(tenant_id=tenant.id, exception_item_id=item.id, outcome=DecisionOutcome.PAY, reason_code="ok",
                                submitted_by_user_id=maker.id, decided_by_user_id=users["approver"].id,
                                decided_at=start + timedelta(hours=3), source=DecisionSource.HUMAN))
    db_session.commit()
    period = (start, start + timedelta(days=1))
    result = control_checks.dual_control(db_session, tenant.id, *period)
    assert result.status == "FAIL" and result.evidence["decisions"] == 2 and result.evidence["without_a_second_person"] == 1
    assert control_checks.dual_control(db_session, tenant.id, start - timedelta(days=1), start).status == "PASS"  # nothing that day


def test_two_person_changes_and_support_access(db_session, tenant_factory):
    from openclear.domain.change_request import ChangeRequest
    from openclear.domain.support_access_grant import SupportAccessGrant

    tenant, _account, users = tenant_factory.make(slug="chk-two")
    when = datetime(2026, 4, 2, 10, tzinfo=timezone.utc)
    db_session.add(ChangeRequest(tenant_id=tenant.id, kind="ml_model.activate", summary="x", payload={}, status="approved",
                                 requested_by_user_id=users["admin"].id, decided_by_user_id=users["admin"].id, decided_at=when))
    db_session.add(SupportAccessGrant(tenant_id=tenant.id, support_user_id=users["viewer"].id, reason="Ticket 12", status="ended",
                                      granted_by_user_id=users["admin"].id, granted_at=when, expires_at=when + timedelta(days=60)))
    db_session.commit()
    day = (when.replace(hour=0), when.replace(hour=0) + timedelta(days=1))
    assert control_checks.two_person_changes(db_session, tenant.id, *day).status == "FAIL"
    support = control_checks.support_access_review(db_session, tenant.id, *day)
    assert support.status == "WARN" and support.evidence["longer_than_30_days"] == 1


def test_state_checks_find_what_needs_doing(db_session, tenant_factory):
    from openclear.domain.webhook import WebhookEndpoint

    tenant, _account, users = tenant_factory.make(slug="chk-state")
    users["viewer"].last_login_at = datetime.now(timezone.utc) - timedelta(days=200)
    db_session.add(WebhookEndpoint(tenant_id=tenant.id, url="http://core.example.com/hook", secret_encrypted="x", event_types=[]))
    db_session.commit()
    dormant = control_checks.dormant_accounts(db_session, tenant.id)
    assert dormant.status == "WARN" and users["viewer"].email in dormant.evidence["no_sign_in_for_90_days"]
    assert control_checks.integration_tls(db_session, tenant.id).status == "FAIL"
    assert control_checks.staff_mfa(db_session, tenant.id).status == "WARN"  # nobody has a security key
    group = security_group_service.create_security_group(db_session, tenant.id, security_group_service.SecurityGroupInput(
        name="Does it all", permissions=["issued_item:write", "exception:decide"], offered_to_customers=True))
    from tests.items import _make_customer_with_account

    customer, _acct = _make_customer_with_account(db_session, tenant, "CK-1")
    user_service.create_user_with_membership(db_session, tenant.id, email="both@ck.example.com", password=TenantFactory.PASSWORD,
                                             security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    assert control_checks.customer_sod(db_session, tenant.id).status == "WARN"
    assert control_checks.default_dispositions(db_session, tenant.id).status == "WARN"


def test_scheduled_jobs_are_recorded_and_checked(db_session, engine, tenant_factory, monkeypatch):
    from openclear.config import get_settings
    from openclear.domain.scheduled_job_run import ScheduledJobRun
    from openclear.workers.leader_lock import run_exclusively

    tenant, _account, _users = tenant_factory.make(slug="chk-jobs")
    assert control_checks.scheduled_jobs(db_session, tenant.id).status == "PASS"  # none configured here
    monkeypatch.setattr(get_settings(), "enable_housekeeping_scheduler", True)
    assert control_checks.scheduled_jobs(db_session, tenant.id).status == "FAIL"  # configured, never run
    run_exclusively("housekeeping_job", lambda: None, engine=engine)
    db_session.expire_all()
    run = db_session.get(ScheduledJobRun, "housekeeping_job")
    assert run.last_ok is True and run.last_finished_at is not None
    assert control_checks.scheduled_jobs(db_session, tenant.id).status == "PASS"


def test_control_response_fails_past_the_grace_period(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="chk-grace")
    _record(db_session, tenant, "sla", "FAIL")
    assert control_checks.control_response(db_session, tenant.id).status == "WARN"
    _deviations(db_session, tenant)[0].opened_at = datetime.now(timezone.utc) - timedelta(hours=30)
    db_session.commit()
    result = control_checks.control_response(db_session, tenant.id)
    assert result.status == "FAIL" and result.evidence["open_beyond_grace"] == ["sla"]


# --- The pages, end to end ---------------------------------------------------------------------


def test_a_fail_that_recovers_its_timeline_deviation_response_and_event(client, db_session, tenant_factory):
    """The plan's manual pass: a bank with a FAIL that recovers."""
    from openclear.services import controls_service

    tenant, _account, users = tenant_factory.make(slug="dev-e2e")
    controls_service.status(db_session, tenant.id)  # first sight of every check
    from openclear.domain.webhook import WebhookEndpoint

    hook = WebhookEndpoint(tenant_id=tenant.id, url="http://core.example.com/hook", secret_encrypted="x", event_types=[])
    db_session.add(hook)
    db_session.commit()
    controls_service.status(db_session, tenant.id)  # integration_tls goes to FAIL
    hook.url = "https://core.example.com/hook"
    db_session.commit()
    controls_service.status(db_session, tenant.id)  # and back
    [deviation] = [d for d in _deviations(db_session, tenant) if d.check_key == "integration_tls"]
    assert deviation.closed_at is not None
    events = db_session.execute(select(SecurityEvent).where(SecurityEvent.tenant_id == tenant.id,
                                                            SecurityEvent.name == "control.status.changed",
                                                            SecurityEvent.target_id == "integration_tls")).scalars().all()
    assert len(events) == 2

    csrf = _login(client, tenant, users["admin"].email)
    assert "Deviations and their responses" in client.get("/ui/admin/controls").text
    listing = client.get("/ui/admin/controls/deviations").text
    assert "Integrations use HTTPS" in listing and "Not yet" in listing
    page = client.get(f"/ui/admin/controls/deviations/{deviation.id}").text
    assert "send data unencrypted" in page
    refused = client.post(f"/ui/admin/controls/deviations/{deviation.id}/respond", data={"csrf_token": csrf, "cause": "", "response": ""},
                          follow_redirects=False)
    assert "Say+what+caused" in refused.headers["location"]
    client.post(f"/ui/admin/controls/deviations/{deviation.id}/respond",
                data={"csrf_token": csrf, "cause": "A test endpoint left on http", "response": "Moved it to https"})
    db_session.expire_all()
    assert db_session.get(ControlDeviation, deviation.id).response == "Moved it to https"
    timeline = client.get("/ui/admin/controls/checks/integration_tls").text
    assert 'class="status-day status-fail"' in timeline and "OC-SC-07" in timeline


def test_who_can_respond_test_a_period_and_see_these_pages(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="dev-perm")
    [failed] = _record(db_session, tenant, "sla", "FAIL")
    [deviation] = _deviations(db_session, tenant)
    reader = security_group_service.create_security_group(db_session, tenant.id, security_group_service.SecurityGroupInput(
        name="Auditor", permissions=["audit_log:read"]))
    user_service.create_user_with_membership(db_session, tenant.id, email="auditor@dev.example.com", password=TenantFactory.PASSWORD,
                                             security_group_id=reader.id)
    db_session.commit()
    csrf = _login(client, tenant, "auditor@dev.example.com")
    assert client.get(f"/ui/admin/controls/deviations/{deviation.id}").status_code == 200
    assert client.post(f"/ui/admin/controls/deviations/{deviation.id}/respond",
                       data={"csrf_token": csrf, "cause": "x", "response": "y"}).status_code == 403
    _login(client, tenant, users["viewer"].email)
    assert client.get("/ui/admin/controls/deviations").status_code == 403

    csrf = _login(client, tenant, users["admin"].email)
    done = client.post("/ui/admin/controls/test-period", data={"csrf_token": csrf, "start": "2026-01-01", "end": "2026-01-31"},
                       follow_redirects=False)
    assert "Tested+2026-01-01" in done.headers["location"]
    db_session.expire_all()
    people = db_session.execute(select(ControlTestResult).where(ControlTestResult.tenant_id == tenant.id,
                                                                ControlTestResult.started_by == "person")).scalars().all()
    assert {r.check_key for r in people} == {c.key for c in checks.registry() if c.kind == "population"}
    assert all(r.period_start.date().isoformat() == "2026-01-01" and r.period_end.date().isoformat() == "2026-02-01" for r in people)
    assert failed.id
