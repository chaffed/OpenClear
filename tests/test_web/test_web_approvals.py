# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import re
from datetime import date
from decimal import Decimal

from openclear.services import ach_return_reason_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def _create_check_exception(client, csrf, account, check_number="1"):
    client.post(
        "/ui/issued-items",
        data={
            "account_id": str(account.id), "check_number": check_number, "amount": "100.00",
            "payee_name": "Vendor", "issue_date": "2026-01-01", "csrf_token": csrf,
        },
    )
    client.post(
        "/ui/paid-items",
        data={
            "account_id": str(account.id), "check_number": check_number, "presented_amount": "999.00",
            "presented_date": "2026-01-10", "csrf_token": csrf,
        },
    )
    queue = client.get("/ui/exceptions")
    return re.search(r"/ui/exceptions/([0-9a-f-]{36})", queue.text).group(1)


def _create_ach_exception(client, csrf, account, trace_number="TRACE0001"):
    client.post(
        "/ui/ach/transactions",
        data={
            "account_id": str(account.id), "originator_id": "UNKNOWN01", "originator_name": "Suspicious LLC",
            "amount": "75.00", "transaction_type": "debit", "sec_code": "WEB", "trace_number": trace_number,
            "effective_date": "2026-01-10", "csrf_token": csrf,
        },
    )
    queue = client.get("/ui/exceptions")
    return re.search(r"/ui/exceptions/([0-9a-f-]{36})", queue.text).group(1)


def test_approvals_nav_link_shown_only_to_decide_permission_holders(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="approvals-nav", require_dual_control=True)

    _login(client, tenant.slug, users["approver"].email)
    assert '/ui/exceptions/approvals"' in client.get("/ui/").text

    _login(client, tenant.slug, users["preparer"].email)
    assert '/ui/exceptions/approvals"' not in client.get("/ui/").text


def test_approvals_page_requires_decide_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="approvals-perm", require_dual_control=True)
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get("/ui/exceptions/approvals", follow_redirects=False)
    assert resp.status_code == 403


def test_approvals_queue_lists_recommendation_with_recommender(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="approvals-list", require_dual_control=True)
    csrf = _login(client, tenant.slug, users["preparer"].email)
    exception_id = _create_check_exception(client, csrf, account)

    client.post(
        f"/ui/exceptions/{exception_id}/recommend",
        data={"outcome": "return", "reason_code": "fraud", "notes": "looks fraudulent", "csrf_token": csrf},
    )

    csrf = _login(client, tenant.slug, users["approver"].email)
    page = client.get("/ui/exceptions/approvals")
    assert page.status_code == 200
    assert "return" in page.text
    assert users["preparer"].email in page.text
    assert "your recommendation" not in page.text


def test_approvals_queue_flags_own_recommendation(client, tenant_factory):
    # Bookkeeper holds both exception:recommend and exception:decide -- the one role
    # where "did I recommend this myself" actually matters for the approvals queue.
    tenant, account, users = tenant_factory.make(slug="approvals-own", require_dual_control=True)
    csrf = _login(client, tenant.slug, users["bookkeeper"].email)
    exception_id = _create_check_exception(client, csrf, account)

    client.post(
        f"/ui/exceptions/{exception_id}/recommend",
        data={"outcome": "pay", "reason_code": "ok", "notes": "", "csrf_token": csrf},
    )

    page = client.get("/ui/exceptions/approvals")
    assert "your recommendation" in page.text


def test_detail_page_hides_decide_form_until_a_recommendation_exists(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="approvals-needs-rec", require_dual_control=True)
    csrf = _login(client, tenant.slug, users["bookkeeper"].email)
    exception_id = _create_check_exception(client, csrf, account)

    detail = client.get(f"/ui/exceptions/{exception_id}")
    assert "Recommend a decision" in detail.text
    assert "Approve recommendation" not in detail.text
    # No second, decide-side form rendered at all -- under dual control with nothing
    # recommended yet, decide() would just reject it with RECOMMENDATION_REQUIRED, so the
    # form shouldn't be there to submit in the first place.
    assert "Submit decision" not in detail.text


def test_detail_page_shows_notice_instead_of_decide_form_for_own_recommendation(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="approvals-self-notice", require_dual_control=True)
    csrf = _login(client, tenant.slug, users["bookkeeper"].email)
    exception_id = _create_check_exception(client, csrf, account)

    client.post(
        f"/ui/exceptions/{exception_id}/recommend",
        data={"outcome": "pay", "reason_code": "ok", "notes": "", "csrf_token": csrf},
    )

    detail = client.get(f"/ui/exceptions/{exception_id}")
    assert "a different approver must finalize it" in detail.text
    assert "Approve recommendation" not in detail.text
    # The recommend card is still there, relabeled for a revision rather than a fresh one.
    assert "Revise recommendation" in detail.text


def test_detail_page_shows_prefilled_decide_form_for_a_different_approver(client, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="approvals-prefill", require_dual_control=True)
    csrf = _login(client, tenant.slug, users["preparer"].email)
    exception_id = _create_check_exception(client, csrf, account)

    client.post(
        f"/ui/exceptions/{exception_id}/recommend",
        data={"outcome": "return", "reason_code": "fraud", "notes": "flagged", "csrf_token": csrf},
    )

    csrf = _login(client, tenant.slug, users["approver"].email)
    detail = client.get(f"/ui/exceptions/{exception_id}")
    assert "Approve recommendation" in detail.text
    assert "<strong>Return</strong> (fraud)" in detail.text  # what was recommended
    assert 'value="flagged"' in detail.text  # the maker's note, carried into the return form
    assert '<details class="decision-return" open>' in detail.text  # the recommended choice, opened

    # And actually approving it (a different user than the recommender) still works end to end.
    resp = client.post(
        f"/ui/exceptions/{exception_id}/decide",
        data={"outcome": "return", "reason_code": "fraud", "notes": "flagged", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]


def test_detail_page_prefills_ach_return_reason_for_approver(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="approvals-ach-prefill", require_dual_control=True)
    reason = ach_return_reason_service.create_ach_return_reason(
        db_session, tenant.id, ach_return_reason_service.AchReturnReasonInput(reason_text="Duplicate Entry", transaction_code="667")
    )
    db_session.commit()

    csrf = _login(client, tenant.slug, users["preparer"].email)
    exception_id = _create_ach_exception(client, csrf, account)
    client.post(
        f"/ui/exceptions/{exception_id}/recommend",
        data={"outcome": "return", "reason_code": "", "notes": "", "ach_return_reason_id": str(reason.id), "csrf_token": csrf},
    )

    _login(client, tenant.slug, users["approver"].email)
    detail = client.get(f"/ui/exceptions/{exception_id}")
    assert f'value="{reason.id}" required checked' in detail.text


def test_single_control_tenant_still_allows_deciding_your_own_recommendation(client, tenant_factory):
    # require_dual_control=False (the default) -- the "you can't approve your own
    # recommendation" notice must NOT appear here, since decide() itself doesn't block
    # self-approval outside dual control.
    tenant, account, users = tenant_factory.make(slug="approvals-single-control")
    csrf = _login(client, tenant.slug, users["bookkeeper"].email)
    exception_id = _create_check_exception(client, csrf, account)

    client.post(
        f"/ui/exceptions/{exception_id}/recommend",
        data={"outcome": "pay", "reason_code": "ok", "notes": "", "csrf_token": csrf},
    )

    detail = client.get(f"/ui/exceptions/{exception_id}")
    assert "a different approver must finalize it" not in detail.text
    assert "Approve recommendation" in detail.text

    resp = client.post(
        f"/ui/exceptions/{exception_id}/decide",
        data={"outcome": "pay", "reason_code": "ok", "notes": "", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" not in resp.headers["location"]
