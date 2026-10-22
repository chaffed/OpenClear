# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The read-only inventories (CONTROLS_PLAN.md CT1): `GET /api/v1/compliance/controls` and
`/cuecs`, and the Controls page grouped by control."""

from openclear.compliance import controls, cuecs
from openclear.services import security_group_service, user_service
from tests.conftest import TenantFactory, login_headers
from tests.items import _make_customer_with_account


def _customer_person(db_session, tenant):
    customer, _account = _make_customer_with_account(db_session, tenant, "CMP-1")
    group = next(g for g in security_group_service.list_security_groups(db_session, tenant.id) if g.name == "Admin")
    user_service.create_user_with_membership(db_session, tenant.id, email="pat@customer.example.com", password=TenantFactory.PASSWORD,
                                             security_group_id=group.id, customer_id=customer.id)
    db_session.commit()
    return "pat@customer.example.com"


def test_the_control_inventory_with_live_results(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="ct1-controls")
    body = client.get("/api/v1/compliance/controls", headers=login_headers(client, tenant.slug, users["admin"].email)).json()
    assert body["version"] == controls.VERSION and body["catalog"] == cuecs.NIST_CATALOG
    assert [c["id"] for c in body["controls"]] == [c.id for c in controls.CONTROLS]
    by_id = {c["id"]: c for c in body["controls"]}

    sod = by_id["OC-AC-05"]
    assert sod["nist"] == [{"id": "AC-5", "title": "Separation of Duties", "oscal_id": "ac-5"}]
    live = next(c for c in sod["checks"] if c["key"] == "segregation_of_duties")
    assert live["status"] == "live" and live["result"] in ("PASS", "WARN", "FAIL") and live["title"] and live["detail"]
    sod_customer = next(c for c in sod["checks"] if c["key"] == "customer_sod")
    assert sod_customer["status"] == "live" and sod_customer["result"] == "PASS"
    assert sod["status"] == max((live["result"], sod_customer["result"]), key=["PASS", "WARN", "FAIL"].index)  # the worst of them
    currency = next(c for c in by_id["OC-CM-02"]["checks"] if c["key"] == "software_currency")
    assert currency["status"] == "live" and currency["result"] == "WARN" and "No release feed is set" in currency["detail"]  # none in tests
    assert sod["cuecs"] == ["B-AC-5-01", "C-AC-5-01"]

    assert by_id["OC-AC-09"]["status"] is None and by_id["OC-AC-09"]["checks"] == []  # nothing checks it yet
    # The worst of its live checks: the action log's chain is checked under two controls alike.
    assert by_id["OC-AU-01"]["status"] == by_id["OC-AU-02"]["status"]


def test_controls_are_for_the_bank_s_own_staff(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="ct1-refused")
    viewer = client.get("/api/v1/compliance/controls", headers=login_headers(client, tenant.slug, users["viewer"].email))
    assert viewer.status_code == 403  # no audit_log:read
    customer = client.get("/api/v1/compliance/controls", headers=login_headers(client, tenant.slug, _customer_person(db_session, tenant)))
    assert customer.status_code == 403


def test_the_cuec_inventory(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="ct1-cuecs")
    headers = login_headers(client, tenant.slug, users["viewer"].email)  # published documentation: any signed-in person
    body = client.get("/api/v1/compliance/cuecs", headers=headers).json()
    assert body["version"] == cuecs.VERSION and len(body["cuecs"]) == len(cuecs.CUECS)
    by_id = {c["id"]: c for c in body["cuecs"]}
    assert "OC-AC-05" in by_id["B-AC-5-01"]["controls"] and by_id["B-AC-5-01"]["check_status"] == "live"
    assert by_id["B-RA-3-01"]["controls"] == [] and by_id["B-RA-3-01"]["check"] is None

    hosts = client.get("/api/v1/compliance/cuecs?party=host", headers=headers).json()["cuecs"]
    assert hosts and {c["party"] for c in hosts} == {"host"}
    assert client.get("/api/v1/compliance/cuecs?party=nobody", headers=headers).status_code == 422


def test_a_customer_sees_only_its_own_cuecs(client, db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="ct1-customer")
    headers = login_headers(client, tenant.slug, _customer_person(db_session, tenant))
    body = client.get("/api/v1/compliance/cuecs", headers=headers).json()
    assert body["cuecs"] and {c["party"] for c in body["cuecs"]} == {"customer"}
    assert len(body["cuecs"]) == len([c for c in cuecs.CUECS if c.party == "customer"])
    assert client.get("/api/v1/compliance/cuecs?party=customer", headers=headers).status_code == 200
    assert client.get("/api/v1/compliance/cuecs?party=bank", headers=headers).status_code == 403


def test_the_controls_page_is_grouped_by_control(client, db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="ct1-page")
    client.get("/ui/login")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": client.cookies.get("csrf_token"), "next": "/ui/"})
    page = client.get("/ui/admin/controls").text
    for label in controls.FAMILIES.values():
        assert f"<h2>{label}</h2>" in page
    assert page.index("<h2>Access control</h2>") < page.index("OC-AC-05") < page.index("<h2>Identification and authentication</h2>")
    assert '<abbr class="nowrap" title="Separation of Duties">AC-5</abbr>' in page
    assert "Action log intact" in page  # a live check's title, under its controls
    assert "Not checked yet" in page and "planned" in page
