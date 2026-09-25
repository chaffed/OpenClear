# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.services import customer_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    return client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )


def _csrf(client):
    return client.cookies.get("csrf_token")


def test_bank_wizard_requires_tenant_manage_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-wizard-forbidden")
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get("/ui/wizard/bank", follow_redirects=False)

    assert resp.status_code == 403


def test_bank_wizard_page_renders_and_shows_progress(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-wizard-render")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/wizard/bank")

    assert resp.status_code == 200
    assert "Set your organization" in resp.text
    assert "required steps complete" in resp.text


def test_acknowledge_and_unacknowledge_bank_step_via_web(client, db_session, tenant_factory):
    from openclear.services import wizard_service

    tenant, _account, users = tenant_factory.make(slug="web-wizard-ack")
    _login(client, tenant.slug, users["admin"].email)
    client.get("/ui/wizard/bank")

    resp = client.post("/ui/wizard/bank/branding/acknowledge", data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert resp.status_code == 303
    db_session.expire_all()
    steps = {v.step.key: v.is_complete for v in wizard_service.get_bank_wizard_steps(db_session, tenant.id)}
    assert steps["branding"] is True

    resp2 = client.post("/ui/wizard/bank/branding/unacknowledge", data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert resp2.status_code == 303
    db_session.expire_all()
    steps = {v.step.key: v.is_complete for v in wizard_service.get_bank_wizard_steps(db_session, tenant.id)}
    assert steps["branding"] is False


def test_dashboard_shows_getting_started_banner_until_complete(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-wizard-dashboard")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/")

    assert "Getting Started checklist" in resp.text


def test_creating_a_customer_redirects_into_its_wizard(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-wizard-customer-redirect")
    _login(client, tenant.slug, users["admin"].email)
    client.get("/ui/customers/new")

    resp = client.post(
        "/ui/customers",
        data={
            "customer_number": "C-1", "name": "Acme Co", "external_customer_id": "", "tax_id": "", "primary_contact_name": "",
            "email": "", "phone": "", "website": "", "address_line1": "", "address_line2": "", "city": "", "state": "",
            "postal_code": "", "notes": "", "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert "/wizard" in resp.headers["location"]


def test_customer_wizard_requires_customer_manage_permission(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-wizard-customer-forbidden")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    db_session.commit()
    _login(client, tenant.slug, users["preparer"].email)

    resp = client.get(f"/ui/customers/{customer.id}/wizard", follow_redirects=False)

    assert resp.status_code == 403


def test_customer_wizard_page_renders(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-wizard-customer-render")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/customers/{customer.id}/wizard")

    assert resp.status_code == 200
    assert "Add this customer" in resp.text


def test_dual_control_toggle_via_web(client, db_session, tenant_factory):
    from openclear.domain.tenant import Tenant

    tenant, _account, users = tenant_factory.make(slug="web-wizard-dualcontrol")
    _login(client, tenant.slug, users["admin"].email)
    client.get("/ui/settings")

    resp = client.post(
        "/ui/settings/dual-control", data={"require_dual_control": "true", "csrf_token": _csrf(client)}, follow_redirects=False
    )
    assert resp.status_code == 303
    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).require_dual_control is True

    resp2 = client.post("/ui/settings/dual-control", data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert resp2.status_code == 303
    db_session.expire_all()
    assert db_session.get(Tenant, tenant.id).require_dual_control is False


def test_every_bank_wizard_step_link_resolves(client, tenant_factory):
    """Regression test: every step's link_url must actually be a real, reachable page --
    catches exactly the class of bug where a step still points at a URL that moved or
    never existed (e.g. wizard_service.py once linked "Add an account" to
    /ui/accounts/new before that page existed, and separately kept pointing SSO at
    /ui/settings/sso after it moved to /ui/admin/sso)."""
    from openclear.services import wizard_service

    tenant, _account, users = tenant_factory.make(slug="web-wizard-links-bank")
    _login(client, tenant.slug, users["admin"].email)

    for step in wizard_service.BANK_STEPS:
        resp = client.get(step.link_url, follow_redirects=False)
        assert resp.status_code in (200, 303), f"{step.key} -> {step.link_url} returned {resp.status_code}"


def test_every_customer_wizard_step_link_resolves(client, db_session, tenant_factory):
    from openclear.services import wizard_service

    tenant, _account, users = tenant_factory.make(slug="web-wizard-links-customer")
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme Co")
    )
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)

    for step in wizard_service.CUSTOMER_STEPS:
        link_url = step.link_url.format(customer_id=customer.id)
        resp = client.get(link_url, follow_redirects=False)
        assert resp.status_code in (200, 303), f"{step.key} -> {link_url} returned {resp.status_code}"
