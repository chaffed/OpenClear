# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""API clients under row-level security (SDK_PLAN.md P0): the token endpoint finds the
client before any user is known, so it must bind to the client's bank from the client id;
and a client only ever sees its own bank's rows."""

from datetime import date
from decimal import Decimal

import pytest

from openclear.services import api_client_service, issued_item_service, security_group_service
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_a_client_signs_in_and_sees_only_its_own_bank(rls_client, db_session, tenant_factory):
    mine, my_account, _ = tenant_factory.make(slug="rls-apic-mine")
    other, other_account, _ = tenant_factory.make(slug="rls-apic-other")
    for tenant, account, number in ((mine, my_account, "MINE-1"), (other, other_account, "OTHER-1")):
        issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
            account_id=account.id, check_number=number, amount=Decimal("5.00"), payee_name="X", issue_date=date(2026, 3, 1),
        ), submitted_by_user_id=None)
    group = security_group_service.get_security_group_by_name(db_session, mine.id, "Admin")
    issued = api_client_service.create_client(db_session, mine.id, name="Core", security_group_id=group.id)
    db_session.commit()

    token = rls_client.post("/api/v1/oauth/token", data={
        "grant_type": "client_credentials", "client_id": issued.client.client_id, "client_secret": issued.secret,
    })
    assert token.status_code == 200, token.text
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}

    items = rls_client.get("/api/v1/issued-items", headers=headers)
    assert items.status_code == 200, items.text
    assert [i["check_number"] for i in items.json()] == ["MINE-1"]


def test_a_client_id_naming_another_bank_finds_nothing(rls_client, db_session, tenant_factory):
    mine, _account, _ = tenant_factory.make(slug="rls-apic-forged-a")
    other, _other_account, _ = tenant_factory.make(slug="rls-apic-forged-b")
    group = security_group_service.get_security_group_by_name(db_session, mine.id, "Admin")
    issued = api_client_service.create_client(db_session, mine.id, name="Core", security_group_id=group.id)
    db_session.commit()

    forged = issued.client.client_id.replace(mine.id.hex, other.id.hex)  # bound to the other bank: the row isn't visible there
    resp = rls_client.post("/api/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": forged, "client_secret": issued.secret})
    assert resp.status_code == 401


def test_an_idempotent_retry_replays_under_rls(rls_client, db_session, tenant_factory):
    tenant, account, _ = tenant_factory.make(slug="rls-apic-idem")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    issued = api_client_service.create_client(db_session, tenant.id, name="Core", security_group_id=group.id)
    db_session.commit()
    token = rls_client.post("/api/v1/oauth/token", data={
        "grant_type": "client_credentials", "client_id": issued.client.client_id, "client_secret": issued.secret,
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "core-batch-42"}
    item = {"account_id": str(account.id), "check_number": "IDEM-1", "amount": "1.00", "payee_name": "X", "issue_date": "2026-03-01"}

    first = rls_client.post("/api/v1/issued-items", headers=headers, json=item)
    retry = rls_client.post("/api/v1/issued-items", headers=headers, json=item)
    assert first.status_code == retry.status_code == 201, first.text
    assert retry.headers["idempotency-replayed"] == "true" and retry.json()["id"] == first.json()["id"]
