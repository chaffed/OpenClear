# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.services import customer_service
from tests.conftest import TenantFactory


def _login(client, tenant_slug, email, password=TenantFactory.PASSWORD):
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post(
        "/ui/login", data={"tenant_slug": tenant_slug, "email": email, "password": password, "csrf_token": csrf, "next": "/ui/"}
    )
    return csrf


def test_customers_page_requires_permission(client, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-forbidden")
    _login(client, tenant.slug, users["viewer"].email)

    resp = client.get("/ui/customers", follow_redirects=False)
    assert resp.status_code == 403


def test_create_customer_via_web(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-create")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/customers", data={"csrf_token": csrf, "customer_number": "C-1", "name": "Acme Client"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/ui/customers")

    list_page = client.get("/ui/customers")
    assert "C-1" in list_page.text
    assert "Acme Client" in list_page.text


def test_create_customer_rejects_duplicate_number(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-dup")
    csrf = _login(client, tenant.slug, users["admin"].email)

    client.post("/ui/customers", data={"csrf_token": csrf, "customer_number": "DUPE", "name": "First"})
    resp = client.post("/ui/customers", data={"csrf_token": csrf, "customer_number": "DUPE", "name": "Second"})
    assert resp.status_code == 422


def test_bulk_upload_accounts_with_customer_number(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-bulk-accounts")
    csrf = _login(client, tenant.slug, users["admin"].email)

    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-9", name="Nine Corp")
    )
    db_session.commit()

    content = (
        "account_number,name,customer_number\n"
        "ACC-1,Assigned Account,C-9\n"
        "ACC-2,Unassigned Account,\n"
    ).encode()

    resp = client.post(
        "/ui/accounts/bulk", data={"csrf_token": csrf}, files={"upload_file": ("accounts.csv", content, "text/csv")}
    )
    assert resp.status_code == 200
    assert "2" in resp.text

    accounts_page = client.get("/ui/accounts")
    assert "ACC-1" in accounts_page.text
    assert "ACC-2" in accounts_page.text
    assert "Nine Corp" in accounts_page.text


def test_bulk_upload_accounts_rejects_unknown_customer_number(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-bulk-accounts-bad")
    csrf = _login(client, tenant.slug, users["admin"].email)

    content = "account_number,name,customer_number\nACC-1,Some Account,DOES-NOT-EXIST\n".encode()

    resp = client.post(
        "/ui/accounts/bulk", data={"csrf_token": csrf}, files={"upload_file": ("accounts.csv", content, "text/csv")}
    )
    assert resp.status_code == 200
    assert "No customer found" in resp.text


def test_bulk_upload_users_with_customer_number(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-bulk-users")
    csrf = _login(client, tenant.slug, users["admin"].email)

    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-5", name="Five Inc")
    )
    db_session.commit()

    content = ("email,security_group,password,customer_number\nscoped@example.com,Preparer,hunter2-hunter2,C-5\n").encode()

    resp = client.post(
        "/ui/users/bulk", data={"csrf_token": csrf}, files={"upload_file": ("users.csv", content, "text/csv")}
    )
    assert resp.status_code == 200
    assert "1 created" in resp.text

    list_page = client.get("/ui/users")
    assert "scoped@example.com" in list_page.text
    assert "Five Inc" in list_page.text


def test_new_user_form_offers_customer_dropdown(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-user-form")
    _login(client, tenant.slug, users["admin"].email)
    customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-7", name="Seven LLC"))
    db_session.commit()

    resp = client.get("/ui/users/new")
    assert resp.status_code == 200
    assert "Seven LLC" in resp.text


def test_create_customer_with_contact_details_via_web(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-contact-create")
    csrf = _login(client, tenant.slug, users["admin"].email)

    resp = client.post(
        "/ui/customers",
        data={
            "csrf_token": csrf,
            "customer_number": "C-10",
            "name": "Contact Co",
            "external_customer_id": "CIF-1",
            "tax_id": "98-7654321",
            "primary_contact_name": "Sam Roe",
            "email": "sam@contactco.example.com",
            "phone": "555-0111",
            "website": "https://contactco.example.com",
            "address_line1": "1 Elm St",
            "city": "Metropolis",
            "state": "NY",
            "postal_code": "10001",
            "notes": "VIP client.",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    customer = customer_service.get_customer_by_number(db_session, tenant.id, "C-10")
    detail_page = client.get(f"/ui/customers/{customer.id}")
    assert detail_page.status_code == 200
    assert "CIF-1" in detail_page.text
    assert "98-7654321" in detail_page.text
    assert "Sam Roe" in detail_page.text
    assert "sam@contactco.example.com" in detail_page.text
    assert "555-0111" in detail_page.text
    assert "Metropolis" in detail_page.text
    assert "VIP client." in detail_page.text


def test_customer_detail_page_lists_its_accounts(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-detail-accounts")
    csrf = _login(client, tenant.slug, users["admin"].email)
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-11", name="Detail Corp")
    )
    db_session.commit()

    client.post(
        "/ui/accounts", data={"csrf_token": csrf, "account_number": "DA-1", "name": "Detail Account", "customer_id": str(customer.id)}
    )

    detail_page = client.get(f"/ui/customers/{customer.id}")
    assert detail_page.status_code == 200
    assert "DA-1" in detail_page.text
    assert "Detail Account" in detail_page.text


def test_customer_detail_page_404s_for_unknown_id(client, tenant_factory):
    import uuid

    tenant, _account, users = tenant_factory.make(slug="web-cust-detail-404")
    _login(client, tenant.slug, users["admin"].email)

    resp = client.get(f"/ui/customers/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_customer_detail_page_404s_for_other_tenants_customer(client, db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="web-cust-detail-cross-a")
    tenant_b, _account_b, users_b = tenant_factory.make(slug="web-cust-detail-cross-b")
    customer_a = customer_service.create_customer(
        db_session, tenant_a.id, customer_service.CustomerInput(customer_number="C-1", name="A Corp")
    )
    db_session.commit()

    _login(client, tenant_b.slug, users_b["admin"].email)
    resp = client.get(f"/ui/customers/{customer_a.id}")
    assert resp.status_code == 404


def test_edit_customer_round_trip(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="web-cust-edit")
    csrf = _login(client, tenant.slug, users["admin"].email)
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number="C-12", name="Edit Me", email="old@example.com")
    )
    db_session.commit()

    edit_form = client.get(f"/ui/customers/{customer.id}/edit")
    assert edit_form.status_code == 200
    assert "old@example.com" in edit_form.text

    resp = client.post(
        f"/ui/customers/{customer.id}",
        data={"csrf_token": csrf, "customer_number": "C-12", "name": "Edited Name", "email": "new@example.com"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    detail_page = client.get(f"/ui/customers/{customer.id}")
    assert "Edited Name" in detail_page.text
    assert "new@example.com" in detail_page.text
    assert "old@example.com" not in detail_page.text
