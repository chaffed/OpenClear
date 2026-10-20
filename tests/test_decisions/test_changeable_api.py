# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""The API for decisions changeable until the cut-off (UI_PLAN.md UI-D13): `locked_at` and
`changeable_until` on a decision, PUT to change it, DELETE to take it back, 409 once final."""

from datetime import datetime, timedelta, timezone

from openclear.services import decision_service
from tests.conftest import login_headers
from tests.test_decisions.test_changeable import _item


def test_change_and_undo_before_the_cutoff_then_409(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-api")
    item = _item(db_session, tenant, account)
    headers = login_headers(client, tenant.slug, users["admin"].email)

    made = client.post(f"/api/v1/exceptions/{item.id}/decide", headers=headers, json={"outcome": "pay", "reason_code": "Reviewed"})
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["locked_at"] is None and body["changeable_until"] is not None and body["change_pending"] is False

    changed = client.put(f"/api/v1/exceptions/{item.id}/decision", headers=headers, json={"outcome": "return", "reason_code": "Stop payment"})
    assert changed.status_code == 200 and changed.json()["outcome"] == "return"
    assert client.delete(f"/api/v1/exceptions/{item.id}/decision", headers=headers).status_code == 204
    assert client.get(f"/api/v1/exceptions/{item.id}/decision", headers=headers).status_code == 404

    client.post(f"/api/v1/exceptions/{item.id}/decide", headers=headers, json={"outcome": "pay", "reason_code": "Reviewed"})
    item.decision_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    decision_service.lock_due(db_session)
    db_session.commit()
    final = client.get(f"/api/v1/exceptions/{item.id}/decision", headers=headers).json()
    assert final["locked_at"] is not None and final["changeable_until"] is None
    refused = client.put(f"/api/v1/exceptions/{item.id}/decision", headers=headers, json={"outcome": "return", "reason_code": "Late"})
    assert refused.status_code == 409 and "final since" in refused.json()["detail"]


def test_under_dual_control_a_put_proposes_and_another_approves(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-api-dual", require_dual_control=True)
    item = _item(db_session, tenant, account)
    preparer = login_headers(client, tenant.slug, users["preparer"].email)
    approver = login_headers(client, tenant.slug, users["approver"].email)
    client.post(f"/api/v1/exceptions/{item.id}/recommend", headers=preparer, json={"outcome": "pay", "reason_code": "Looks right"})
    client.post(f"/api/v1/exceptions/{item.id}/decide", headers=approver, json={"outcome": "pay", "reason_code": "Looks right"})

    proposed = client.put(f"/api/v1/exceptions/{item.id}/decision", headers=preparer, json={"outcome": "return", "reason_code": "Stop payment"})
    assert proposed.status_code == 200 and proposed.json()["outcome"] == "pay" and proposed.json()["change_pending"] is True
    assert client.delete(f"/api/v1/exceptions/{item.id}/decision", headers=approver).status_code == 409
    approved = client.put(f"/api/v1/exceptions/{item.id}/decision", headers=approver, json={"outcome": "return", "reason_code": "Stop payment"})
    assert approved.status_code == 200 and approved.json()["outcome"] == "return" and approved.json()["change_pending"] is False
