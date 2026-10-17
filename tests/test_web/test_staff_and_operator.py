# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Bank staff and the platform operator (UX_PLAN.md U7, U8, U9; MASTER_PLAN.md M6): the
customer hub and "send a reminder now", platform staff signing in, the operator view and
setting up a bank, and bank-granted support access from grant to expiry."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.bulk_upload_file import BulkUploadFile, BulkUploadKind
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.support_access_grant import SupportAccessGrant
from openclear.domain.tenant import Tenant
from openclear.domain.user import User
from openclear.services import customer_hub_service, customer_service, security_group_service, support_access_service, user_service
from tests.conftest import TenantFactory
from tests.test_ml.test_per_customer_ml import _make_exception

STAFF_PASSWORD = "Platform-Staff-Pass-1"


def _login(client, slug, email):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": slug, "email": email, "password": TenantFactory.PASSWORD, "csrf_token": csrf, "next": "/ui/"})
    return csrf


def _platform_login(client, email, password=STAFF_PASSWORD):
    client.get("/ui/platform/login")
    csrf = client.cookies.get("csrf_token")
    resp = client.post("/ui/platform/login", data={"email": email, "password": password, "csrf_token": csrf}, follow_redirects=False)
    return resp, csrf


def _staff(db_session, email, role):
    user, _created = support_access_service.set_platform_role(db_session, email, role, password=STAFF_PASSWORD)
    db_session.commit()
    return user


# --- The customer hub ------------------------------------------------------------------------


def test_the_customer_hub_puts_customers_with_items_first(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="hub")
    busy = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="H-2", name="Zulu Busy Co"))
    quiet = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="H-1", name="Alpha Quiet Co"))
    db_session.commit()
    exception = _make_exception(db_session, tenant, account, users, "9101", "100.00", "150.00")
    exception.customer_id = busy.id
    exception.decision_deadline = datetime.now(timezone.utc) - timedelta(minutes=5)  # due today at any hour (an hour ahead is tomorrow after 23:00 UTC)
    db_session.add(BulkUploadFile(tenant_id=tenant.id, customer_id=busy.id, kind=BulkUploadKind.ISSUED_ITEMS, original_filename="i.csv",
                                  storage_path="x", size_bytes=1, sha256_hex="0" * 64, signature_hex="0",
                                  uploaded_at=datetime.now(timezone.utc) - timedelta(days=2)))
    db_session.commit()

    rows = customer_hub_service.rows(db_session, tenant.id)
    assert [r.customer.name for r in rows] == ["Zulu Busy Co", "Alpha Quiet Co"]
    assert (rows[0].open_count, rows[0].due_today, rows[0].decision_default) == (1, 1, "Stays open")
    assert [r.customer.name for r in customer_hub_service.rows(db_session, tenant.id, quiet_days=1)] == ["Zulu Busy Co", "Alpha Quiet Co"]
    assert [r.customer.name for r in customer_hub_service.rows(db_session, tenant.id, quiet_days=7)] == ["Alpha Quiet Co"]

    csrf = _login(client, tenant.slug, users["admin"].email)
    page = client.get("/ui/customers").text
    assert page.index("Zulu Busy Co") < page.index("Alpha Quiet Co") and "2 days ago" in page and "never" in page
    assert f"/ui/exceptions?customer={busy.id}" in page
    filtered = client.get(f"/ui/exceptions?customer={busy.id}").text
    assert "9101" in filtered or "150.00" in filtered
    assert "No customers match" in client.get("/ui/customers?q=nothing-like-this").text

    sent = client.post(f"/ui/customers/{busy.id}/remind", data={"csrf_token": csrf}, follow_redirects=True)
    assert "Reminder sent to" in sent.text
    reminders = db_session.execute(select(Notification).where(Notification.notification_type == NotificationType.DEADLINE_REMINDER)).scalars().all()
    assert reminders and all("1 item to decide" in n.subject for n in reminders)
    assert db_session.execute(select(AuditLogEntry).where(AuditLogEntry.action == "customer.remind")).scalar_one()


# --- Platform staff ------------------------------------------------------------------------------


def test_platform_sign_in_is_for_platform_staff_only(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="platform-signin")
    assert client.get("/ui/platform", follow_redirects=False).headers["location"] == "/ui/platform/login"
    resp, _ = _platform_login(client, users["admin"].email, TenantFactory.PASSWORD)  # a bank admin isn't platform staff
    assert resp.status_code == 401
    _staff(db_session, "ops@platform.example", "operator")
    assert _platform_login(client, "ops@platform.example", "wrong")[0].status_code == 401
    resp, _ = _platform_login(client, "ops@platform.example")
    assert resp.status_code == 303 and client.cookies.get("platform_session")
    home = client.get("/ui/platform").text
    assert "Set up a bank" in home and tenant.name in home

    client.cookies.clear()
    _staff(db_session, "help@platform.example", "support")
    _platform_login(client, "help@platform.example")
    assert "Set up a bank" not in client.get("/ui/platform").text
    assert client.get(f"/ui/platform/banks/{tenant.id}/controls").status_code == 403


def test_an_operator_sets_up_a_bank(client, db_session, tenant_factory):
    _existing, _account, users = tenant_factory.make(slug="platform-existing")
    _staff(db_session, "ops2@platform.example", "operator")
    _, csrf = _platform_login(client, "ops2@platform.example")
    bad = client.post("/ui/platform/banks", data={"csrf_token": csrf, "name": "X", "slug": "Not A Slug", "admin_email": "a@x.example"})
    assert bad.status_code == 422 and "lowercase" in bad.text
    taken = client.post("/ui/platform/banks", data={"csrf_token": csrf, "name": "X", "slug": "new-bank", "admin_email": users["admin"].email})
    assert taken.status_code == 422 and "already signs in" in taken.text
    asked = client.post("/ui/platform/banks", data={"csrf_token": csrf, "name": "New Bank", "slug": "new-bank", "admin_email": "first@newbank.example"},
                        follow_redirects=False)
    assert asked.status_code == 303 and "/ui/platform/changes" in asked.headers["location"]  # a second operator sets it up (DUAL_CONTROL_PLAN.md)
    assert db_session.execute(select(Tenant).where(Tenant.slug == "new-bank")).first() is None
    from openclear.services import change_request_service

    [change] = change_request_service.pending(db_session, None)
    assert client.post(f"/ui/platform/changes/{change.id}/approve", data={"csrf_token": csrf}).status_code == 403  # not their own
    _staff(db_session, "ops3@platform.example", "operator")
    _, csrf = _platform_login(client, "ops3@platform.example")
    made = client.post(f"/ui/platform/changes/{change.id}/approve", data={"csrf_token": csrf})
    assert made.status_code == 200 and "/ui/login/new-bank" in made.text
    tenant = db_session.execute(select(Tenant).where(Tenant.slug == "new-bank")).scalar_one()
    admin = db_session.execute(select(User).where(User.email == "first@newbank.example")).scalar_one()
    assert admin.must_change_password
    assert db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id, AuditLogEntry.action == "tenant.provision")).scalar_one()


# --- Support access --------------------------------------------------------------------------------


def _second_admin(db_session, tenant):
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    user = user_service.create_user_with_membership(db_session, tenant.id, email=f"second@{tenant.slug}.example",
                                                    password=TenantFactory.PASSWORD, security_group_id=group.id)
    db_session.commit()
    return user


def test_support_access_from_grant_to_end(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="support-flow")
    exception = _make_exception(db_session, tenant, account, users, "9201", "100.00", "150.00")
    helper = _staff(db_session, "helper@platform.example", "support")

    csrf = _login(client, tenant.slug, users["admin"].email)
    assert 'data-key="support-access"' in client.get("/ui/settings").text
    resp = client.post("/ui/settings/support-access", data={"csrf_token": csrf, "support_email": helper.email,
                                                            "reason": "Ticket 77: import stuck", "hours": "4"}, follow_redirects=True)
    assert "Access granted." in resp.text
    grant = db_session.execute(select(SupportAccessGrant)).scalar_one()
    assert grant.status == "active" and not grant.write_access

    # The support person opens the bank, under their own name, read-only.
    client.cookies.clear()
    _, pcsrf = _platform_login(client, helper.email)
    assert "Ticket 77" in client.get("/ui/platform").text
    opened = client.post(f"/ui/platform/support/{grant.id}/start", data={"csrf_token": pcsrf}, follow_redirects=False)
    assert opened.status_code == 303 and opened.headers["location"] == "/ui/"
    page = client.get(f"/ui/exceptions/{exception.id}")
    assert page.status_code == 200 and "Support access" in page.text and "read-only" in page.text
    assert client.get("/ui/users", follow_redirects=False).status_code == 403
    assert client.get("/ui/settings", follow_redirects=False).status_code == 403
    assert client.get("/ui/audit-log", follow_redirects=False).status_code == 403
    decided = client.post(f"/ui/exceptions/{exception.id}/decide", data={"csrf_token": client.cookies.get("csrf_token"), "outcome": "pay"},
                          follow_redirects=False)
    assert decided.status_code == 403
    actions = [e.action for e in db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id)).scalars()]
    assert "support_access.grant" in actions and "support_access.use" in actions

    # The bank ends it: the session stops on its next request.
    revoked = support_access_service.revoke(db_session, tenant.id, grant.id, user_id=users["admin"].id)
    db_session.commit()
    assert revoked.status == "revoked"
    assert client.get(f"/ui/exceptions/{exception.id}", follow_redirects=False).status_code == 303


def test_write_access_needs_a_second_admin_and_nobody_grants_themselves(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="support-write")
    helper = _staff(db_session, "writer@platform.example", "support")
    second = _second_admin(db_session, tenant)
    grant = support_access_service.grant(db_session, tenant.id, support_email=helper.email, reason="Fix a bad upload", hours=2,
                                         write_access=True, granted_by_user_id=users["admin"].id)
    assert grant.status == "pending" and support_access_service.active_grant(db_session, grant.id, user_id=helper.id) is None
    with pytest.raises(support_access_service.SupportAccessError, match="second admin"):
        support_access_service.approve(db_session, tenant.id, grant.id, approver_user_id=users["admin"].id)
    support_access_service.approve(db_session, tenant.id, grant.id, approver_user_id=second.id)
    assert grant.status == "active"
    assert "issued_item:write" in support_access_service.permissions_for(grant)
    assert not {"exception:decide", "exception:recommend", "user:manage", "tenant:manage", "audit_log:read"} & support_access_service.permissions_for(grant)

    # A platform person who is also a bank admin can't let themselves in.
    _staff(db_session, users["admin"].email, "support")
    with pytest.raises(support_access_service.SupportAccessError, match="yourself"):
        support_access_service.grant(db_session, tenant.id, support_email=users["admin"].email, reason="Let me in", hours=1,
                                     write_access=False, granted_by_user_id=users["admin"].id)
    with pytest.raises(support_access_service.SupportAccessError, match="isn't a platform support person"):
        support_access_service.grant(db_session, tenant.id, support_email=users["viewer"].email, reason="Not staff", hours=1,
                                     write_access=False, granted_by_user_id=users["admin"].id)


def test_access_expires_on_time_and_goes_in_the_evidence(db_session, tenant_factory):
    import datetime as dt

    from openclear.services import evidence_service

    tenant, _account, users = tenant_factory.make(slug="support-expire")
    helper = _staff(db_session, "expiring@platform.example", "support")
    grant = support_access_service.grant(db_session, tenant.id, support_email=helper.email, reason="Look at the controls", hours=1,
                                         write_access=False, granted_by_user_id=users["admin"].id)
    db_session.commit()
    assert support_access_service.expire_due(db_session, now=datetime.now(timezone.utc) + timedelta(hours=2)) == 1
    db_session.commit()
    assert grant.status == "expired"
    assert db_session.execute(select(AuditLogEntry).where(AuditLogEntry.action == "support_access.expire")).scalar_one().channel.value == "system"
    today = dt.date.today()
    bundle = evidence_service.build(db_session, tenant.id, start=today - dt.timedelta(days=1), end=today + dt.timedelta(days=1),
                                    generated_by_user_id=users["admin"].id)
    assert bundle["support_access"][0]["support_person"] == helper.email and bundle["support_access"][0]["status"] == "expired"


def test_platform_pages_and_the_support_banner_in_a_browser(client, db_session, tenant_factory):
    from pathlib import Path

    playwright = pytest.importorskip("playwright.sync_api")
    try:
        runner = playwright.sync_playwright().start()
        browser = runner.chromium.launch()
    except Exception as exc:  # noqa: BLE001 -- no browser installed here
        pytest.skip(f"Chromium isn't available: {exc}")

    tenant, _account, users = tenant_factory.make(slug="support-browser")
    operator = _staff(db_session, "ops3@platform.example", "operator")
    grant = support_access_service.grant(db_session, tenant.id, support_email=operator.email, reason="Check the new bank", hours=2,
                                         write_access=False, granted_by_user_id=users["admin"].id)
    db_session.commit()
    _, csrf = _platform_login(client, operator.email)
    axe = (Path(__file__).resolve().parent.parent / "fixtures" / "axe" / "axe.min.js").read_text()

    def serve(route):
        request = route.request
        # A page's redirect is followed here (a browser can't be handed one by a route); the
        # test client keeps the cookies, so the browser doesn't need the Set-Cookie headers.
        response = client.request(request.method, request.url.replace("http://testserver", ""),
                                  headers={k: v for k, v in request.headers.items() if k.lower() != "cookie"},
                                  content=request.post_data_buffer, follow_redirects=request.is_navigation_request())
        headers = {k: v for k, v in response.headers.items() if k.lower() != "set-cookie"}
        route.fulfill(status=response.status_code, headers=headers, body=response.content)

    def problems(page) -> list[str]:
        page.add_script_tag(content=axe)
        results = page.evaluate("axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
        return [f"{v['id']}: {n['target']}" for v in results["violations"] for n in v["nodes"]]

    try:
        context = browser.new_context(viewport={"width": 390, "height": 844}, bypass_csp=True)
        context.route("http://testserver/**", serve)
        page = context.new_page()
        for path in ("/ui/platform", "/ui/platform/banks/new"):
            page.goto("http://testserver" + path)
            assert problems(page) == [], path
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"{path} scrolls sideways"
        page.goto("http://testserver/ui/platform")
        page.wait_for_function("!document.querySelector('[data-lazy-state]') || document.querySelector('[data-lazy-state]').textContent !== 'Checking…'")
        page.click(f'form[action="/ui/platform/support/{grant.id}/start"] button')
        page.wait_for_selector(".support-banner")
        assert page.locator(".support-banner").count() == 1 and "read-only" in page.inner_text(".support-banner")
        assert problems(page) == []
        page.click(".support-banner button")
        page.wait_for_selector("text=Access banks have granted you")
    finally:
        browser.close()
        runner.stop()
