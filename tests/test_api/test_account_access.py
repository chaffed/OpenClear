# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Per-account access (db/account_scope.py; services/account_access_service.py): a
customer's person limited to some of the customer's accounts sees, changes and hears about
only those, through the API, the web pages, notifications and their own bulk uploads."""

import io
import uuid

import pytest
from sqlalchemy import select

from openclear.db import account_scope
from openclear.db.base import Base
from openclear.domain.notification import Notification
from openclear.domain.tenant_membership import TenantMembership
from openclear.services import account_access_service, account_service, customer_service, security_group_service, user_service
from tests.conftest import TenantFactory, login_headers


def _setup(db_session, tenant_factory, slug="acc"):
    tenant, _house, users = tenant_factory.make(slug=slug)
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    mine = account_service.create_account(db_session, tenant.id, account_service.AccountInput(
        account_number="ACC-MINE", name="Payroll", customer_id=customer.id))
    other = account_service.create_account(db_session, tenant.id, account_service.AccountInput(
        account_number="ACC-OTHER", name="Treasury", customer_id=customer.id))
    people = {}
    for name, group in (("preparer", "Preparer"), ("approver", "Approver"), ("colleague", "Preparer")):
        people[name] = user_service.create_user_with_membership(
            db_session, tenant.id, email=f"{name}.limited@{slug}.example.com", password=TenantFactory.PASSWORD,
            security_group_id=security_group_service.get_security_group_by_name(db_session, tenant.id, group).id, customer_id=customer.id,
        )
    db_session.flush()
    for name in ("preparer", "approver"):  # limited to one account; the colleague keeps all
        membership = _membership(db_session, tenant, people[name])
        account_access_service.set_access(db_session, tenant.id, membership.id, all_accounts=False, account_ids=[mine.id])
    db_session.commit()
    return tenant, users, customer, mine, other, people


def _membership(db_session, tenant, user):
    return db_session.execute(select(TenantMembership).where(TenantMembership.tenant_id == tenant.id,
                                                             TenantMembership.user_id == user.id)).scalar_one()


def _present(client, headers, account, number):
    """A check with no issued item: an exception."""
    resp = client.post("/api/v1/paid-items", headers=headers, json={
        "account_id": str(account.id), "check_number": number, "presented_amount": "10.00", "presented_date": "2026-01-01"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_limited_person_sees_and_changes_only_their_accounts(client, db_session, tenant_factory):
    tenant, users, _customer, mine, other, people = _setup(db_session, tenant_factory)
    admin = login_headers(client, tenant.slug, users["admin"].email)
    paid_mine, paid_other = _present(client, admin, mine, "101"), _present(client, admin, other, "202")
    preparer = login_headers(client, tenant.slug, people["preparer"].email)
    colleague = login_headers(client, tenant.slug, people["colleague"].email)

    assert [a["account_number"] for a in client.get("/api/v1/accounts", headers=preparer).json()] == ["ACC-MINE"]
    assert {a["account_number"] for a in client.get("/api/v1/accounts", headers=colleague).json()} == {"ACC-MINE", "ACC-OTHER"}
    assert [p["id"] for p in client.get("/api/v1/paid-items", headers=preparer).json()] == [paid_mine["id"]]
    assert client.get(f"/api/v1/paid-items/{paid_other['id']}", headers=preparer).status_code == 404
    assert client.get(f"/api/v1/paid-items/{paid_other['id']}", headers=colleague).status_code == 200

    exceptions = client.get("/api/v1/exceptions", headers=preparer).json()
    assert [e["source_item_id"] for e in exceptions] == [paid_mine["id"]]
    hidden = next(e for e in client.get("/api/v1/exceptions", headers=colleague).json() if e["source_item_id"] == paid_other["id"])
    assert client.get(f"/api/v1/exceptions/{hidden['id']}", headers=preparer).status_code == 404
    assert client.post(f"/api/v1/exceptions/{hidden['id']}/recommend", headers=preparer,
                       json={"outcome": "pay", "reason_code": "test"}).status_code == 404

    item = {"check_number": "9", "amount": "5.00", "payee_name": "Vendor", "issue_date": "2026-01-01"}
    assert client.post("/api/v1/issued-items", headers=preparer, json={**item, "account_id": str(other.id)}).status_code == 404
    assert client.post("/api/v1/issued-items", headers=preparer, json={**item, "account_id": str(mine.id)}).status_code == 201
    assert client.post("/api/v1/stop-payments", headers=preparer, json={
        "account_id": str(other.id), "check_number": "1", "effective_date": "2026-01-01"}).status_code == 404

    # Decisions on another account's exceptions stay out of sight too.
    approver = login_headers(client, tenant.slug, people["approver"].email)
    bank_approver = login_headers(client, tenant.slug, users["admin"].email)
    assert client.post(f"/api/v1/exceptions/{hidden['id']}/decide", headers=bank_approver,
                       json={"outcome": "pay", "reason_code": "test"}).status_code == 200
    assert client.get(f"/api/v1/exceptions/{hidden['id']}/decision", headers=approver).status_code == 404


def test_the_web_pages_show_only_their_accounts(client, db_session, tenant_factory):
    tenant, users, _customer, mine, other, people = _setup(db_session, tenant_factory, slug="acc-web")
    admin = login_headers(client, tenant.slug, users["admin"].email)
    _present(client, admin, mine, "101")
    _present(client, admin, other, "202")
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": people["preparer"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    for path in ("/ui/accounts", "/ui/paid-items", "/ui/exceptions", "/ui/", "/ui/issued-items"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert "ACC-OTHER" not in page.text and "Treasury" not in page.text, path
    assert "ACC-MINE" in client.get("/ui/accounts").text


def test_notifications_reach_only_those_who_can_see_the_account(client, db_session, tenant_factory):
    tenant, users, _customer, mine, other, people = _setup(db_session, tenant_factory, slug="acc-notify")
    admin = login_headers(client, tenant.slug, users["admin"].email)
    _present(client, admin, other, "202")
    db_session.expire_all()

    def told(user):
        return db_session.execute(select(Notification).where(Notification.recipient_user_id == user.id)).scalars().all()

    assert told(people["colleague"]) and not told(people["preparer"])
    _present(client, admin, mine, "101")
    db_session.expire_all()
    assert told(people["preparer"])


def test_a_limited_persons_bulk_upload_reaches_only_their_accounts(db_session, tenant_factory):
    from openclear.domain.bulk_upload_file import BulkUploadKind
    from openclear.services import bulk_import_service, issued_item_service
    from openclear.workers import jobs

    tenant, _users, customer, mine, other, people = _setup(db_session, tenant_factory, slug="acc-bulk")
    data = ("account_number,check_number,amount,payee_name,issue_date\n"
            "ACC-MINE,11,10.00,Vendor,2026-03-02\nACC-OTHER,12,10.00,Vendor,2026-03-02\n").encode()
    record = bulk_import_service.receive_upload(
        db_session, tenant.id, kind=BulkUploadKind.ISSUED_ITEMS, file_format="tabular", filename="i.csv", content_type=None,
        uploaded_by_user_id=people["preparer"].id, customer_id=customer.id, stream=io.BytesIO(data),
    )
    db_session.commit()
    jobs.drain(db_session.get_bind())
    db_session.expire_all()
    numbers = {i.check_number: i.account_id for i in issued_item_service.list_issued_items(db_session, tenant.id)}
    assert numbers == {"11": mine.id}
    assert db_session.get(type(record), record.id).failed_count == 1


def test_bank_staff_choose_the_accounts_on_the_web_and_through_the_api(client, db_session, tenant_factory):
    tenant, users, customer, mine, other, people = _setup(db_session, tenant_factory, slug="acc-admin")
    membership = _membership(db_session, tenant, people["colleague"])
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})
    assert "All of the customer&#39;s accounts" in client.get(f"/ui/users/{membership.id}/edit").text
    page = client.get(f"/ui/users/{membership.id}/accounts")
    assert page.status_code == 200 and "ACC-OTHER" in page.text
    assert client.post(f"/ui/users/{membership.id}/accounts", data={"csrf_token": csrf, "scope": "some"}).status_code == 422
    saved = client.post(f"/ui/users/{membership.id}/accounts", data={"csrf_token": csrf, "scope": "some", "account_id": str(other.id)},
                        follow_redirects=False)
    assert saved.status_code == 303
    db_session.expire_all()
    access = account_access_service.get_access(db_session, tenant.id, membership.id)
    assert (access.all_accounts, access.account_ids) == (False, {other.id})
    colleague = login_headers(client, tenant.slug, people["colleague"].email)
    assert [a["account_number"] for a in client.get("/api/v1/accounts", headers=colleague).json()] == ["ACC-OTHER"]

    admin = login_headers(client, tenant.slug, users["admin"].email)
    review = {row["membership_id"]: row for row in client.get("/api/v1/users", headers=admin).json()}
    assert review[str(membership.id)]["all_accounts"] is False and review[str(membership.id)]["account_ids"] == [str(other.id)]
    put = client.put(f"/api/v1/users/{membership.id}/accounts", headers=admin, json={"all_accounts": True})
    assert put.status_code == 200 and put.json()["all_accounts"] is True
    assert len(client.get("/api/v1/accounts", headers=colleague).json()) == 2  # at once
    foreign = account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number="X-9", name="Elsewhere"))
    db_session.commit()
    bad = client.put(f"/api/v1/users/{membership.id}/accounts", headers=admin, json={"all_accounts": False, "account_ids": [str(foreign.id)]})
    assert bad.status_code == 422
    bank_wide = _membership(db_session, tenant, users["viewer"])
    assert client.get(f"/api/v1/users/{bank_wide.id}/accounts", headers=admin).status_code == 404


def test_moving_to_another_customer_resets_the_accounts(db_session, tenant_factory):
    tenant, _users, customer, _mine, _other, people = _setup(db_session, tenant_factory, slug="acc-move")
    membership = _membership(db_session, tenant, people["preparer"])
    elsewhere = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-2", name="Beta"))
    user_service.update_membership(db_session, tenant.id, membership.id, security_group_id=membership.security_group_id,
                                   customer_id=elsewhere.id)
    db_session.commit()
    assert membership.all_accounts is True and account_access_service.chosen_accounts(db_session, membership.id) == frozenset()


def test_every_table_with_an_account_is_limited():
    """A new table carrying an account's rows must be added to db/account_scope.py."""
    covered = {option.entity.class_ if hasattr(option.entity, "class_") else option.root_entity
               for option in account_scope._criteria((uuid.uuid4(),), uuid.uuid4())}
    covered_names = {getattr(c, "__tablename__", None) for c in covered}
    exempt = {"membership_account", "billing_invoice", "billing_run", "tenant"}  # the grant itself; the bank's own billing
    with_accounts = {mapper.class_.__tablename__ for mapper in Base.registry.mappers
                     if "account_id" in mapper.columns or mapper.class_.__tablename__ == "account"}
    assert with_accounts - exempt <= covered_names, with_accounts - exempt - covered_names


@pytest.mark.parametrize("unbound", [True, False])
def test_unlimited_sessions_are_untouched(db_session, tenant_factory, unbound):
    tenant, _users, _customer, mine, other, _people = _setup(db_session, tenant_factory, slug=f"acc-free-{unbound}")
    if not unbound:
        account_scope.bind_session_to_accounts(db_session, [mine.id], user_id=None)
        account_scope.bind_session_to_accounts(db_session, None)
    assert {a.id for a in account_service.list_accounts(db_session, tenant.id)} >= {mine.id, other.id}
