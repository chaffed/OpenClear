# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Setting the decision cut-off (UI_PLAN.md UI-D13): the bank's in the control panel, a
customer's on its page, both protected changes; the bank's closures; the hub's Cut-off column."""

from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.change_request import ChangeRequest
from openclear.domain.tenant import Tenant
from openclear.services import cutoff_service, security_group_service, user_service
from tests.conftest import TenantFactory
from tests.test_ml.test_per_customer_ml import _make_customer_with_account
from tests.test_web.test_mobile_components import _sign_in


def _save(client, csrf, **fields):
    data = {"csrf_token": csrf, "time_zone": "America/New_York", "cutoff_check": "11:00", "cutoff_ach": "15:00",
            "min_review_minutes": "120", **fields}
    return client.post("/ui/settings/cutoff", data=data, follow_redirects=False)


def test_the_bank_sets_its_cutoff_in_the_control_panel(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cutoff-bank")
    csrf = _sign_in(client, tenant, users)
    assert "Decision cut-off" in client.get("/ui/settings").text and "Not set" in client.get("/ui/settings").text
    page = client.get("/ui/settings/cutoff")
    assert page.status_code == 200 and "America/New York" in page.text

    assert _save(client, csrf).status_code == 303
    db_session.expire_all()
    saved = db_session.get(Tenant, tenant.id)
    assert (saved.time_zone, saved.decision_cutoffs, saved.cutoff_min_review_minutes) == ("America/New_York",
                                                                                        {"check": "11:00", "ach": "15:00"}, 120)
    actions = [e.action for e in db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id)).scalars()]
    assert "tenant.update_cutoffs" in actions

    refused = _save(client, csrf, cutoff_check="25:00")
    assert refused.status_code == 422 and "24-hour HH:MM" in refused.text.replace("&#39;", "'")


def test_with_sensitive_settings_protected_it_waits_for_a_second_admin(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cutoff-dual")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    user_service.create_user_with_membership(db_session, tenant.id, email="second@cutoff-dual-bank.example.com",
                                             password=TenantFactory.PASSWORD, security_group_id=group.id)
    tenant.sensitive_changes_need_approval = True
    db_session.commit()
    csrf = _sign_in(client, tenant, users)

    assert _save(client, csrf).status_code == 303
    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).decision_cutoffs == {}
    assert db_session.execute(select(ChangeRequest).where(ChangeRequest.kind == "decision_cutoff")).scalar_one().status == "pending"


def test_closures_are_added_and_removed(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cutoff-closed")
    csrf = _sign_in(client, tenant, users)
    client.post("/ui/settings/cutoff/closures", data={"csrf_token": csrf, "closed_on": "2026-12-24", "name": "Christmas Eve"})
    [closure] = cutoff_service.closures(db_session, tenant.id)
    assert "Christmas Eve" in client.get("/ui/settings/cutoff").text
    again = client.post("/ui/settings/cutoff/closures", data={"csrf_token": csrf, "closed_on": "2026-12-24", "name": "Again"})
    assert "already a closure" in again.text
    client.post(f"/ui/settings/cutoff/closures/{closure.id}/delete", data={"csrf_token": csrf})
    assert cutoff_service.closures(db_session, tenant.id) == []


def test_a_customers_agreed_cutoff_on_its_page_and_in_the_hub(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cutoff-cust")
    customer, _cust_account = _make_customer_with_account(db_session, tenant, "AGREED-1")
    csrf = _sign_in(client, tenant, users)
    _save(client, csrf)

    page = client.get(f"/ui/customers/{customer.id}")
    assert "Cut-off" in page.text and "The bank's: 11:00" in page.text
    client.post(f"/ui/customers/{customer.id}/cutoff", data={"csrf_token": csrf, "cutoff_check": "14:30", "cutoff_ach": ""})
    assert cutoff_service.customer_cutoffs(db_session, tenant.id, customer.id) == {"check": "14:30"}
    hub = client.get("/ui/customers").text
    assert "Checks 14:30" in hub and "agreed" in hub and "ACH 15:00" in hub
