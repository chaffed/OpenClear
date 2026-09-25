# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import zipfile
from io import BytesIO

from openclear.services import security_group_service, user_service
from openclear.services.security_group_service import SecurityGroupInput
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    return client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )


def _csrf(client):
    return client.cookies.get("csrf_token")


def _make_exporter_user(db_session, tenant, email="exporter@example.com"):
    group = security_group_service.create_security_group(
        db_session, tenant.id, SecurityGroupInput(name="Exporter", permissions=["data_export:run", "tenant:manage", "customer:manage"])
    )
    user = user_service.create_user_with_membership(
        db_session, tenant.id, email=email, password=TenantFactory.PASSWORD, security_group_id=group.id
    )
    db_session.commit()
    return user


def test_admin_lacks_data_export_permission_by_default(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-export-admin-forbidden")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get("/ui/settings/data-export", follow_redirects=False)

    assert resp.status_code == 403


def test_full_export_flow_via_web(client, db_session, tenant_factory):
    tenant, account, _users = tenant_factory.make(slug="web-export-flow")
    exporter = _make_exporter_user(db_session, tenant)
    _login(client, tenant.slug, exporter.email)

    list_resp = client.get("/ui/settings/data-export")
    assert list_resp.status_code == 200
    assert "No exports yet" in list_resp.text

    start_resp = client.post(
        "/ui/settings/data-export/start", data={"confirm": "true", "csrf_token": _csrf(client)}, follow_redirects=False
    )
    assert start_resp.status_code == 303
    assert "error" not in start_resp.headers["location"]

    list_resp2 = client.get("/ui/settings/data-export")
    assert "completed" in list_resp2.text
    assert "Download" in list_resp2.text

    import re

    job_id = re.search(r"/ui/settings/data-export/([0-9a-f-]{36})/download", list_resp2.text).group(1)
    download_resp = client.get(f"/ui/settings/data-export/{job_id}/download")
    assert download_resp.status_code == 200
    assert download_resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(download_resp.content)) as zf:
        assert "data/tenant.json" in zf.namelist()


def test_start_export_without_confirmation_checkbox_shows_error(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-export-noconfirm")
    exporter = _make_exporter_user(db_session, tenant)
    _login(client, tenant.slug, exporter.email)
    client.get("/ui/settings/data-export")

    resp = client.post("/ui/settings/data-export/start", data={"csrf_token": _csrf(client)}, follow_redirects=False)

    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_download_404s_for_a_job_that_does_not_exist(client, db_session, tenant_factory):
    import uuid

    tenant, _account, _users = tenant_factory.make(slug="web-export-missing")
    exporter = _make_exporter_user(db_session, tenant)
    _login(client, tenant.slug, exporter.email)

    resp = client.get(f"/ui/settings/data-export/{uuid.uuid4()}/download", follow_redirects=False)

    assert resp.status_code == 404


def test_customer_scoped_export_flow_via_web(client, db_session, tenant_factory):
    from openclear.services import customer_service

    tenant, _account, _users = tenant_factory.make(slug="web-export-customer")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    db_session.commit()
    exporter = _make_exporter_user(db_session, tenant)
    _login(client, tenant.slug, exporter.email)

    start_resp = client.post(
        f"/ui/customers/{customer.id}/data-export/start", data={"confirm": "true", "csrf_token": _csrf(client)}, follow_redirects=False
    )
    assert start_resp.status_code == 303
    assert "error" not in start_resp.headers["location"]

    list_resp = client.get(f"/ui/customers/{customer.id}/data-export")
    assert "completed" in list_resp.text

    import re

    job_id = re.search(r"/data-export/([0-9a-f-]{36})/download", list_resp.text).group(1)
    download_resp = client.get(f"/ui/customers/{customer.id}/data-export/{job_id}/download")
    assert download_resp.status_code == 200
    with zipfile.ZipFile(BytesIO(download_resp.content)) as zf:
        assert "data/customer.json" in zf.namelist()
        assert "data/audit_log.json" not in zf.namelist()


def test_cross_tenant_download_404s(client, db_session, tenant_factory):
    tenant_a, _account_a, _users_a = tenant_factory.make(slug="web-export-xtenant-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="web-export-xtenant-b")
    exporter_a = _make_exporter_user(db_session, tenant_a, email="exp-a@example.com")
    exporter_b = _make_exporter_user(db_session, tenant_b, email="exp-b@example.com")

    _login(client, tenant_a.slug, exporter_a.email)
    client.post("/ui/settings/data-export/start", data={"confirm": "true", "csrf_token": _csrf(client)}, follow_redirects=False)
    list_resp = client.get("/ui/settings/data-export")
    import re

    job_id = re.search(r"/ui/settings/data-export/([0-9a-f-]{36})/download", list_resp.text).group(1)

    client.cookies.clear()
    _login(client, tenant_b.slug, exporter_b.email)
    resp = client.get(f"/ui/settings/data-export/{job_id}/download", follow_redirects=False)

    assert resp.status_code == 404


def _make_customer_scoped_exporter(db_session, tenant, email="cust-exporter@example.com"):
    """A customer's own user whose (misconfigured or deliberate) group grants
    data_export:run — the permission is intentionally NOT masked for customer-scoped
    sessions, so the routes themselves must confine it to that one customer."""
    from openclear.services import customer_service

    own = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="OWN", name="Own Co"))
    other = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="OTH", name="Other Co"))
    group = security_group_service.create_security_group(
        db_session, tenant.id, SecurityGroupInput(name="Customer Exporter", permissions=["data_export:run"])
    )
    user_service.create_user_with_membership(
        db_session, tenant.id, email=email, password=TenantFactory.PASSWORD, security_group_id=group.id, customer_id=own.id
    )
    db_session.commit()
    return own, other, email


def _export_job_id_for(client, db_session, tenant, customer_id):
    """Creates a completed export for `customer_id` as a separate bank-wide exporter, so a
    test can then check whether a *different* user can reach it."""
    import re

    from fastapi.testclient import TestClient

    bank_client = TestClient(client.app)
    exporter = _make_exporter_user(db_session, tenant, email=f"bank-exporter-{customer_id.hex[:6]}@example.com")
    _login(bank_client, tenant.slug, exporter.email)
    bank_client.post(
        f"/ui/customers/{customer_id}/data-export/start", data={"confirm": "true", "csrf_token": _csrf(bank_client)}
    )
    page = bank_client.get(f"/ui/customers/{customer_id}/data-export").text
    return re.search(r"/data-export/([0-9a-f-]{36})/download", page).group(1)


def test_customer_scoped_user_cannot_use_bank_wide_export(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-export-scoped-bankwide")
    _own, _other, email = _make_customer_scoped_exporter(db_session, tenant)
    _login(client, tenant.slug, email)

    assert client.get("/ui/settings/data-export", follow_redirects=False).status_code == 403
    start = client.post(
        "/ui/settings/data-export/start", data={"confirm": "true", "csrf_token": _csrf(client)}, follow_redirects=False
    )
    assert start.status_code == 403
    assert client.get(f"/ui/settings/data-export/{'0' * 8}-0000-0000-0000-{'0' * 12}/download").status_code == 403


def test_customer_scoped_user_cannot_reach_another_customers_export(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-export-scoped-other")
    _own, other, email = _make_customer_scoped_exporter(db_session, tenant)
    other_job_id = _export_job_id_for(client, db_session, tenant, other.id)
    _login(client, tenant.slug, email)

    assert client.get(f"/ui/customers/{other.id}/data-export").status_code == 404
    start = client.post(
        f"/ui/customers/{other.id}/data-export/start", data={"confirm": "true", "csrf_token": _csrf(client)}, follow_redirects=False
    )
    assert start.status_code == 404
    assert client.get(f"/ui/customers/{other.id}/data-export/{other_job_id}/download").status_code == 404


def test_customer_scoped_user_can_export_own_customer(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="web-export-scoped-own")
    own, _other, email = _make_customer_scoped_exporter(db_session, tenant)
    _login(client, tenant.slug, email)

    start = client.post(
        f"/ui/customers/{own.id}/data-export/start", data={"confirm": "true", "csrf_token": _csrf(client)}, follow_redirects=False
    )
    assert start.status_code == 303
    assert "error" not in start.headers["location"]

    import re

    page = client.get(f"/ui/customers/{own.id}/data-export").text
    job_id = re.search(r"/data-export/([0-9a-f-]{36})/download", page).group(1)
    download = client.get(f"/ui/customers/{own.id}/data-export/{job_id}/download")
    assert download.status_code == 200
    with zipfile.ZipFile(BytesIO(download.content)) as zf:
        assert "data/customer.json" in zf.namelist()
        assert "data/tenant.json" not in zf.namelist()
