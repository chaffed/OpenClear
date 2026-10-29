# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom fields with decisions (plans/CUSTOM_FIELDS_PLAN.md CF6, CF-D7): a destination names the
fields it wants, in order, and gets nothing else. The feed by its request, the OpenClear CSV
decision file by its settings; the NACHA file has nowhere for them. Webhooks stay thin."""

import csv
import io

import pytest
from sqlalchemy import select

from openclear import storage
from openclear.domain.decision import DecisionOutcome
from openclear.domain.webhook import WebhookDelivery
from openclear.services import custom_field_service as cf, decision_outbox_service, security_group_service, user_service, webhook_service
from openclear.services.custom_field_service import FieldInput
from tests.conftest import TenantFactory, login_headers
from tests.items import _decide, _make_customer_with_account, _make_exception


def _define(db_session, tenant, owner, key, **kw):
    data = {"label": key.replace("_", " ").capitalize(), "field_type": "text"}
    data.update(kw)
    cf.create_field(db_session, tenant.id, owner, FieldInput(key=key, **data), user_id=None)
    db_session.commit()


def _setup(db_session, tenant_factory, slug):
    tenant, _house, users = tenant_factory.make(slug=slug)
    customer, account = _make_customer_with_account(db_session, tenant, f"{slug.upper()}-1")
    _define(db_session, tenant, "account", "agg_id", shown_to_customer=True)
    _define(db_session, tenant, "account", "officer_code")
    _define(db_session, tenant, "customer", "tier")
    cf.set_values(db_session, tenant.id, "account", account.id, {"agg_id": "=G-1", "officer_code": "JS"}, user_id=None)
    cf.set_values(db_session, tenant.id, "customer", customer.id, {"tier": "Gold"}, user_id=None)
    db_session.commit()
    _decide(db_session, tenant, users, _make_exception(db_session, tenant, account, users, "501", "1.00", "2.00"), DecisionOutcome.RETURN)
    return tenant, customer, account, users


def test_the_feed_carries_only_the_fields_asked_for(client, db_session, tenant_factory):
    tenant, customer, account, users = _setup(db_session, tenant_factory, "cfd-feed")
    headers = login_headers(client, tenant.slug, users["admin"].email)

    [plain] = client.get("/api/v1/decisions", headers=headers).json()
    assert "custom_fields" not in plain
    [entry] = client.get("/api/v1/decisions?custom_fields=customer.tier,agg_id", headers=headers).json()
    assert list(entry["custom_fields"].items()) == [("customer.tier", "Gold"), ("agg_id", "=G-1")]  # in the order asked

    report = client.get("/api/v1/decisions/feed.csv?custom_fields=agg_id,customer.tier", headers=headers).text
    header, row = report.splitlines()[:2]
    assert header.endswith(",cf_agg_id,cf_customer.tier")
    assert row.endswith(",'=G-1,Gold")  # a value people typed can't run in a spreadsheet

    bad = client.get("/api/v1/decisions?custom_fields=nope,tier", headers=headers)
    assert bad.status_code == 422 and "No such field: nope, tier" in bad.json()["detail"]


def test_a_customers_reader_gets_only_fields_shown_to_it(client, db_session, tenant_factory):
    tenant, customer, account, users = _setup(db_session, tenant_factory, "cfd-cust")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    person = user_service.create_user_with_membership(db_session, tenant.id, email="ap@cfd-cust.example.com", password=TenantFactory.PASSWORD,
                                                      security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    headers = login_headers(client, tenant.slug, person.email)
    [entry] = client.get("/api/v1/decisions?custom_fields=agg_id,officer_code,customer.tier", headers=headers).json()
    assert entry["custom_fields"] == {"agg_id": "=G-1", "officer_code": None, "customer.tier": None}


def test_the_decision_file_adds_its_settings_fields_at_the_end(client, db_session, tenant_factory):
    tenant, _customer, _account, users = _setup(db_session, tenant_factory, "cfd-file")
    with pytest.raises(decision_outbox_service.OutboxError, match="No such field: tier"):
        decision_outbox_service.save(db_session, tenant.id, enabled=True, layout="openclear_csv", interval_minutes=60,
                                     settings={"custom_fields": "tier"})
    decision_outbox_service.save(db_session, tenant.id, enabled=True, layout="openclear_csv", interval_minutes=60,
                                 settings={"custom_fields": " officer_code ,customer.tier,officer_code"})
    assert decision_outbox_service.layout_settings(db_session, tenant.id, "openclear_csv") == {"custom_fields": "officer_code, customer.tier"}
    webhook_service.create_endpoint(db_session, tenant.id, url="https://hooks.example.com/x", event_types=["decision_batch.ready", "exception.decided"])
    batch = decision_outbox_service.run(db_session, tenant.id)
    db_session.commit()

    rows = list(csv.DictReader(io.StringIO(storage.read(batch.storage_ref).decode())))
    assert list(rows[0])[-2:] == ["cf_officer_code", "cf_customer.tier"]
    assert (rows[0]["cf_officer_code"], rows[0]["cf_customer.tier"]) == ("JS", "Gold")
    headers = login_headers(client, tenant.slug, users["admin"].email)
    on_demand = client.get("/api/v1/decisions/file?layout=openclear_csv", headers=headers).text
    assert on_demand.splitlines()[0].endswith(",cf_officer_code,cf_customer.tier")
    # Webhooks stay thin: ids, never values.
    payloads = [d.payload for d in db_session.execute(select(WebhookDelivery)).scalars()]
    assert payloads and not any("JS" in str(p) or "Gold" in str(p) for p in payloads)
