# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom fields in the pages (plans/CUSTOM_FIELDS_PLAN.md CF2): defining them under Settings (a
sensitive change), filling them in on customers and accounts, seeing them in lists, details and
the exception page, and what a customer's person sees."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from openclear.domain.change_request import ChangeRequest
from openclear.domain.custom_field import CustomField
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.services import change_request_service, custom_field_service as cf, security_group_service, user_service
from openclear.services.custom_field_service import FieldInput
from tests.conftest import TenantFactory
from tests.items import _make_customer_with_account, shows_number
from tests.test_web.test_staff_and_operator import _login


def _define(db_session, tenant, owner, key, **kw):
    data = {"label": key.replace("_", " ").capitalize(), "field_type": "text"}
    data.update(kw)
    definition = cf.create_field(db_session, tenant.id, owner, FieldInput(key=key, **data), user_id=None)
    db_session.commit()
    return definition


def _field_form(csrf, **kw):
    data = {"csrf_token": csrf, "owner": "account", "key": "officer_code", "label": "Officer code", "field_type": "text"}
    data.update(kw)
    return data


def test_defining_fields_under_settings(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cfw-define")
    csrf = _login(client, tenant.slug, users["admin"].email)
    page = client.get("/ui/settings/custom-fields")
    assert page.status_code == 200 and "None yet" in page.text

    saved = client.post("/ui/settings/custom-fields", data=_field_form(csrf, required="1", show_in_list="1"), follow_redirects=False)
    assert saved.status_code == 303
    definition = db_session.execute(select(CustomField).where(CustomField.tenant_id == tenant.id)).scalar_one()
    assert definition.key == "officer_code" and definition.required and definition.show_in_list
    refused = client.post("/ui/settings/custom-fields", data=_field_form(csrf, key="Bad Key"))
    assert refused.status_code == 422 and "A key is 1 to 40 characters" in refused.text

    edited = client.post("/ui/settings/custom-fields", data=_field_form(csrf, field_id=str(definition.id), key="ignored", label="Account officer",
                                                                       max_length="12"), follow_redirects=False)
    assert edited.status_code == 303
    db_session.expire_all()
    definition = db_session.get(CustomField, definition.id)
    assert definition.label == "Account officer" and definition.key == "officer_code" and definition.max_length == 12
    assert "Account officer" in client.get("/ui/settings/custom-fields").text

    archived = client.post(f"/ui/settings/custom-fields/{definition.id}/archive", data={"csrf_token": csrf, "archived": "1"}, follow_redirects=False)
    assert archived.status_code == 303
    db_session.expire_all()
    assert db_session.get(CustomField, definition.id).archived_at is not None


def test_a_definition_waits_for_a_second_admin_when_the_bank_asks(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cfw-dual")
    tenant.sensitive_changes_need_approval = True
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    second = user_service.create_user_with_membership(db_session, tenant.id, email="second@cfw-dual.example.com",
                                                      password=TenantFactory.PASSWORD, security_group_id=group.id)
    db_session.commit()
    csrf = _login(client, tenant.slug, users["admin"].email)
    client.post("/ui/settings/custom-fields", data=_field_form(csrf), follow_redirects=False)
    assert db_session.execute(select(CustomField).where(CustomField.tenant_id == tenant.id)).scalar_one_or_none() is None
    change = db_session.execute(select(ChangeRequest).where(ChangeRequest.tenant_id == tenant.id, ChangeRequest.kind == "custom_field.save")).scalar_one()
    assert "Waiting for a second person" in client.get("/ui/settings/custom-fields").text
    change_request_service.decide(db_session, tenant.id, change.id, approve=True, user_id=second.id)
    db_session.commit()
    assert db_session.execute(select(CustomField).where(CustomField.tenant_id == tenant.id)).scalar_one().key == "officer_code"


def test_customers_carry_their_fields_through_the_form_detail_and_list(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cfw-customer")
    _define(db_session, tenant, "customer", "officer_code", required=True, show_in_list=True)
    _define(db_session, tenant, "customer", "tier", field_type="choice", options=["Gold", "Silver"])
    csrf = _login(client, tenant.slug, users["admin"].email)
    assert 'name="cf_officer_code"' in client.get("/ui/customers/new").text

    base = {"csrf_token": csrf, "customer_number": "C-9", "name": "Nine Co", "cf_tier": "Gold"}
    refused = client.post("/ui/customers", data=base)
    assert refused.status_code == 422 and "Required: Officer code" in refused.text and 'value="Gold"' in refused.text
    created = client.post("/ui/customers", data={**base, "cf_officer_code": "JS"}, follow_redirects=False)
    assert created.status_code == 303
    from openclear.services import customer_service

    customer = customer_service.get_customer_by_number(db_session, tenant.id, "C-9")
    assert cf.values_of(db_session, tenant.id, "customer", customer.id) == {"officer_code": "JS", "tier": "Gold"}

    detail = client.get(f"/ui/customers/{customer.id}").text
    assert "Officer code" in detail and "JS" in detail and "Gold" in detail
    edit = client.get(f"/ui/customers/{customer.id}/edit").text
    assert 'value="JS"' in edit and "selected" in edit
    client.post(f"/ui/customers/{customer.id}", data={**base, "cf_officer_code": "MK", "cf_tier": ""}, follow_redirects=False)
    assert cf.values_of(db_session, tenant.id, "customer", customer.id) == {"officer_code": "MK", "tier": None}

    listed = client.get("/ui/customers").text
    assert "<th" in listed and "Officer code" in listed and "MK" in listed
    assert "Nine Co" in client.get("/ui/customers?q=MK").text  # the hub finds a custom value
    assert "Nine Co" not in client.get("/ui/customers?q=ZZZ").text
    other = client.post("/ui/customers", data={**base, "customer_number": "C-10", "name": "Ten Co", "cf_officer_code": "x"}, follow_redirects=False)
    assert other.status_code == 303
    cf.set_values(db_session, tenant.id, "customer", customer_service.get_customer_by_number(db_session, tenant.id, "C-10").id,
                  {"officer_code": None}, user_id=None)
    db_session.commit()
    lacking = client.get("/ui/customers?missing=1").text
    assert "Ten Co" in lacking and "Nine Co" not in lacking


def test_accounts_carry_their_fields_and_the_list_filters_by_them(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="cfw-account")
    _define(db_session, tenant, "account", "agg_id", show_in_list=True, unique=True)
    _define(db_session, tenant, "account", "opened", field_type="date")
    csrf = _login(client, tenant.slug, users["admin"].email)

    created = client.post("/ui/accounts", data={"csrf_token": csrf, "account_number": "7001", "name": "New", "cf_agg_id": "G-7", "cf_opened": "2026-01-02"},
                          follow_redirects=False)
    assert created.status_code == 303
    from openclear.services import account_service

    new = account_service.get_account_by_number(db_session, tenant.id, "7001")
    assert cf.values_of(db_session, tenant.id, "account", new.id) == {"agg_id": "G-7", "opened": "2026-01-02"}
    duplicate = client.post("/ui/accounts", data={"csrf_token": csrf, "account_number": "7002", "name": "Dup", "cf_agg_id": "G-7"})
    assert duplicate.status_code == 400 and "already on another account" in duplicate.text

    page = client.get(f"/ui/accounts/{account.id}/fields")
    assert page.status_code == 200 and 'name="cf_agg_id"' in page.text
    saved = client.post(f"/ui/accounts/{account.id}/fields", data={"csrf_token": csrf, "cf_agg_id": "G-1", "cf_opened": ""}, follow_redirects=False)
    assert saved.status_code == 303
    assert cf.values_of(db_session, tenant.id, "account", account.id)["agg_id"] == "G-1"
    bad = client.post(f"/ui/accounts/{account.id}/fields", data={"csrf_token": csrf, "cf_agg_id": "G-1", "cf_opened": "yesterday"})
    assert bad.status_code == 422 and "YYYY-MM-DD" in bad.text

    listed = client.get("/ui/accounts").text
    assert "Agg id" in listed and "G-1" in listed and "G-7" in listed and "Custom fields</a>" in listed
    filtered = client.get("/ui/accounts?field=agg_id&value=G-7").text
    assert shows_number(filtered, "7001") and not shows_number(filtered, account.account_number)


def test_a_customers_person_sees_only_whats_shown_to_the_customer(client, db_session, tenant_factory):
    tenant, _house, _users = tenant_factory.make(slug="cfw-scope")
    customer, mine = _make_customer_with_account(db_session, tenant, "CFW-1")
    _define(db_session, tenant, "account", "officer_code")
    _define(db_session, tenant, "account", "agg_id", shown_to_customer=True, show_in_list=True)
    cf.set_values(db_session, tenant.id, "account", mine.id, {"officer_code": "SECRET-JS", "agg_id": "G-1"}, user_id=None)
    paid = ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=mine.id, check_number="4242", presented_amount=Decimal("50.00"),
                                                                      presented_date=date.today()))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    person = user_service.create_user_with_membership(db_session, tenant.id, email="ap@cfw-scope.example.com", password=TenantFactory.PASSWORD,
                                                      security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    from openclear.repositories.exception_repo import ExceptionRepository

    exception = ExceptionRepository(db_session, tenant.id).list(source_item_id=paid.id)[0]

    _login(client, tenant.slug, person.email)
    listed = client.get("/ui/accounts").text
    assert "G-1" in listed and "SECRET-JS" not in listed and "Custom fields</a>" not in listed
    assert client.get(f"/ui/accounts/{mine.id}/fields").status_code == 404  # the bank's page, not the customer's
    page = client.get(f"/ui/exceptions/{exception.id}").text
    assert "Agg id" in page and "G-1" in page and "SECRET-JS" not in page
    new_form = client.get("/ui/accounts/new").text
    assert "cf_" not in new_form  # a customer's person never writes them

    client.cookies.clear()
    _login(client, tenant.slug, _users["admin"].email)
    page = client.get(f"/ui/exceptions/{exception.id}").text
    assert "Officer code" in page and "SECRET-JS" in page
