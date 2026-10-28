# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom fields in the API, the data export and the account bulk upload
(plans/CUSTOM_FIELDS_PLAN.md CF4): values as strings, keys left out untouched, a customer-scoped
caller reads only what's shown to it and writes nothing."""

import json
import zipfile

from openclear.services import account_service, custom_field_service as cf, data_export_service, security_group_service, user_service
from openclear.services.custom_field_service import FieldInput
from tests.conftest import TenantFactory, login_headers
from tests.items import _make_customer_with_account
from tests.test_auth.test_data_export_service import _read_json, _run_job_sync


def _define(db_session, tenant, owner, key, **kw):
    data = {"label": key.replace("_", " ").capitalize(), "field_type": "text"}
    data.update(kw)
    definition = cf.create_field(db_session, tenant.id, owner, FieldInput(key=key, **data), user_id=None)
    db_session.commit()
    return definition


def test_definitions_and_values_as_strings(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="cfa-api")
    _define(db_session, tenant, "account", "agg_id", unique=True)
    _define(db_session, tenant, "account", "opened", field_type="date")
    _define(db_session, tenant, "customer", "tier", field_type="choice", options=["Gold", "Silver"], shown_to_customer=True)
    headers = login_headers(client, tenant.slug, users["admin"].email)

    listed = client.get("/api/v1/custom-fields", headers=headers).json()
    assert [(f["owner"], f["key"], f["field_type"]) for f in listed] == [("customer", "tier", "choice"), ("account", "agg_id", "text"), ("account", "opened", "date")]
    assert listed[0]["options"] == ["Gold", "Silver"] and listed[1]["unique"] is True

    created = client.put("/api/v1/accounts/5001", headers=headers,
                         json={"name": "Ops", "custom_fields": {"agg_id": "G-5", "opened": "2026-02-03"}})
    assert created.status_code == 201 and created.json()["custom_fields"] == {"agg_id": "G-5", "opened": "2026-02-03"}
    # A sync that leaves the keys out (or custom_fields entirely) changes nothing.
    synced = client.put("/api/v1/accounts/5001", headers=headers, json={"name": "Ops renamed"})
    assert synced.status_code == 200 and synced.json()["custom_fields"] == {"agg_id": "G-5", "opened": "2026-02-03"}
    cleared = client.put("/api/v1/accounts/5001", headers=headers, json={"name": "Ops", "custom_fields": {"opened": None}})
    assert cleared.json()["custom_fields"] == {"agg_id": "G-5", "opened": None}
    bad = client.put("/api/v1/accounts/5001", headers=headers, json={"name": "Ops", "custom_fields": {"opened": "soon"}})
    assert bad.status_code == 422 and "YYYY-MM-DD" in bad.json()["detail"]
    duplicate = client.put("/api/v1/accounts/5002", headers=headers, json={"name": "Two", "custom_fields": {"agg_id": "G-5"}})
    assert duplicate.status_code == 422 and "already on another account" in duplicate.json()["detail"]
    assert account_service.get_account_by_number(db_session, tenant.id, "5002") is None  # nothing half-saved

    customer = client.put("/api/v1/customers/C-77", headers=headers, json={"name": "Seven", "custom_fields": {"tier": "Gold"}})
    assert customer.status_code == 201 and customer.json()["custom_fields"] == {"tier": "Gold"}
    accounts = {a["account_number"]: a["custom_fields"] for a in client.get("/api/v1/accounts", headers=headers).json()}
    assert accounts["5001"] == {"agg_id": "G-5", "opened": None}
    customers = {c["customer_number"]: c["custom_fields"] for c in client.get("/api/v1/customers", headers=headers).json()}
    assert customers["C-77"] == {"tier": "Gold"}


def test_a_customer_scoped_caller_reads_whats_shown_and_writes_nothing(client, db_session, tenant_factory):
    tenant, _house, _users = tenant_factory.make(slug="cfa-scope")
    customer, mine = _make_customer_with_account(db_session, tenant, "CFA-1")
    _define(db_session, tenant, "account", "officer_code")
    _define(db_session, tenant, "account", "agg_id", shown_to_customer=True)
    cf.set_values(db_session, tenant.id, "account", mine.id, {"officer_code": "SECRET", "agg_id": "G-1"}, user_id=None)
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    person = user_service.create_user_with_membership(db_session, tenant.id, email="ap@cfa-scope.example.com", password=TenantFactory.PASSWORD,
                                                      security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    headers = login_headers(client, tenant.slug, person.email)

    assert [f["key"] for f in client.get("/api/v1/custom-fields", headers=headers).json()] == ["agg_id"]
    [account] = client.get("/api/v1/accounts", headers=headers).json()
    assert account["custom_fields"] == {"agg_id": "G-1"}
    refused = client.put(f"/api/v1/accounts/{mine.account_number}", headers=headers,
                         json={"name": mine.name, "customer_number": customer.customer_number, "custom_fields": {"agg_id": "G-9"}})
    assert refused.status_code == 403
    assert cf.values_of(db_session, tenant.id, "account", mine.id)["agg_id"] == "G-1"


def test_the_data_export_carries_definitions_and_values(db_session, tenant_factory):
    tenant, _house, users = tenant_factory.make(slug="cfa-export")
    customer, mine = _make_customer_with_account(db_session, tenant, "CFA-X")
    _define(db_session, tenant, "account", "officer_code")
    _define(db_session, tenant, "account", "agg_id", shown_to_customer=True)
    cf.set_values(db_session, tenant.id, "account", mine.id, {"officer_code": "JS", "agg_id": "G-1"}, user_id=None)
    db_session.commit()

    def exported(customer_id):
        job = data_export_service.request_export(db_session, tenant.id, users["admin"].id, customer_id=customer_id)
        db_session.commit()
        _run_job_sync(db_session, job)
        db_session.expire_all()
        with zipfile.ZipFile(db_session.get(type(job), job.id).archive_path) as zf:
            return _read_json(zf, "data/custom_fields.json"), _read_json(zf, "data/custom_field_values.json")

    fields, values = exported(None)
    assert sorted(f["key"] for f in fields) == ["agg_id", "officer_code"] and sorted(v["text_value"] for v in values) == ["G-1", "JS"]
    fields, values = exported(customer.id)
    assert [f["key"] for f in fields] == ["agg_id"] and [v["text_value"] for v in values] == ["G-1"]  # what's shown to the customer


def test_the_account_bulk_upload_takes_cf_columns(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cfa-bulk")
    _define(db_session, tenant, "account", "agg_id")
    _define(db_session, tenant, "account", "opened", field_type="date")
    results = account_service.create_accounts_from_rows(db_session, tenant.id, [
        {"account_number": "8001", "name": "A", "cf_agg_id": "G-8", "cf_opened": "2026-01-01"},
        {"account_number": "8002", "name": "B", "cf_agg_id": "", "cf_opened": "not a date"},
    ])
    assert results[0].success and not results[1].success and "YYYY-MM-DD" in results[1].error
    a = account_service.get_account_by_number(db_session, tenant.id, "8001")
    assert cf.values_of(db_session, tenant.id, "account", a.id) == {"agg_id": "G-8", "opened": "2026-01-01"}
    assert account_service.get_account_by_number(db_session, tenant.id, "8002") is None  # the whole row failed
    assert json.dumps(results[0].created_id, default=str)
