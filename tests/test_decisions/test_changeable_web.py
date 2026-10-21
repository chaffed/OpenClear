# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""The screens for decisions changeable until the cut-off (UI_PLAN.md UI-D13): the item says
until when, Change decision and Take it back, the toast's Undo after moving on, the list's
"Decided, changeable", and under dual control a change recommended and approved."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from openclear.domain.decision import DecisionOutcome
from openclear.domain.exception_item import ExceptionStatus
from openclear.services import decision_service
from tests.test_decisions.test_changeable import _decide, _item
from tests.test_web.test_mobile_components import _sign_in
from tests.test_web.test_web_exception_review import _ctx
from tests.test_web.test_web_full_flow import _login


def test_change_and_take_back_on_the_item(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-web")
    item = _item(db_session, tenant, account)
    csrf = _sign_in(client, tenant, users)
    _decide(db_session, tenant, item, users["admin"])

    page = client.get(f"/ui/exceptions/{item.id}").text
    assert "You can change this until" in page and "Change decision" in page and "Take it back" in page
    changed = client.post(f"/ui/exceptions/{item.id}/decision/change", data={"csrf_token": csrf, "outcome": "return", "reason_code": "Altered"},
                          follow_redirects=True)
    assert "Changed to return." in changed.text and "Earlier versions of this decision" in changed.text
    client.post(f"/ui/exceptions/{item.id}/decision/undo", data={"csrf_token": csrf})
    db_session.expire_all()
    assert item.status == ExceptionStatus.OPEN

    _decide(db_session, tenant, item, users["admin"])
    item.decision_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    decision_service.lock_due(db_session)
    db_session.commit()
    page = client.get(f"/ui/exceptions/{item.id}").text
    assert "Final since" in page and "Change decision" not in page


def test_undo_in_the_toast_after_moving_on(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-toast")
    first, second = _item(db_session, tenant, account, "6001"), _item(db_session, tenant, account, "6002")
    csrf = _login(client, tenant.slug, users["admin"].email) and client.cookies.get("csrf_token")
    landed = client.post(f"/ui/exceptions/{first.id}/decide", data={"csrf_token": csrf, "outcome": "pay", "from": "/ui/exceptions"},
                         follow_redirects=False).headers["location"]
    assert urlsplit(landed).path == f"/ui/exceptions/{second.id}"
    assert parse_qs(urlsplit(landed).query)["undo_item"] == [str(first.id)]
    page = client.get(landed).text
    assert 'id="undo-last"' in page and f'/ui/exceptions/{first.id}/decision/undo' in page

    # A link naming someone else's decision gets no Undo form.
    _decide(db_session, tenant, second, users["approver"])
    crafted = client.get(f"/ui/exceptions?flash=Hi&undo=undo-last&undo_kind=decision&undo_item={second.id}").text
    assert 'id="undo-last"' not in crafted
    assert "Check #6001" in client.get("/ui/exceptions?status=changeable").text


def test_under_dual_control_a_change_is_recommended_then_approved(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="chg-web-dual", require_dual_control=True)
    item = _item(db_session, tenant, account)
    decision_service.submit_recommendation(db_session, tenant.id, item.id, _ctx(tenant, users["preparer"]),
                                           outcome=DecisionOutcome.PAY, reason_code="Looks right", notes=None)
    _decide(db_session, tenant, item, users["approver"])

    _login(client, tenant.slug, users["preparer"].email)
    csrf = client.cookies.get("csrf_token")
    assert "Recommend a change" in client.get(f"/ui/exceptions/{item.id}").text
    proposed = client.post(f"/ui/exceptions/{item.id}/decision/change", data={"csrf_token": csrf, "outcome": "return", "reason_code": "Altered"},
                           follow_redirects=True).text
    assert "someone else approves the change" in proposed and "A change is waiting for approval" in proposed

    client.cookies.clear()
    _login(client, tenant.slug, users["approver"].email)
    csrf = client.cookies.get("csrf_token")
    assert "Approve the change" in client.get(f"/ui/exceptions/{item.id}").text
    client.post(f"/ui/exceptions/{item.id}/decision/change", data={"csrf_token": csrf, "outcome": "return", "reason_code": "Altered"})
    db_session.expire_all()
    assert item.status == ExceptionStatus.RETURN and item.change_recommended_at is None
