# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Teller verification under row-level security (TELLER_PLAN.md, Testing): a bank's teller
platform can't verify, or learn anything about, another bank's checks."""

from datetime import date
from decimal import Decimal

import pytest

from openclear.services import account_service, api_client_service, issued_item_service, security_group_service
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_another_banks_check_is_not_on_positive_pay(rls_client, db_session, tenant_factory):
    mine, _my_account, _ = tenant_factory.make(slug="rls-teller-mine")
    other, _account, _ = tenant_factory.make(slug="rls-teller-other")
    other_account = account_service.create_account(db_session, other.id, account_service.AccountInput(account_number="OTHER-77", name="Theirs"))
    issued_item_service.create_issued_item(db_session, other.id, issued_item_service.IssuedItemInput(
        account_id=other_account.id, check_number="7777", amount=Decimal("80.00"), payee_name="X", issue_date=date(2026, 10, 1),
    ), submitted_by_user_id=None)
    group = security_group_service.get_security_group_by_name(db_session, mine.id, "Teller platform")
    issued = api_client_service.create_client(db_session, mine.id, name="Teller", security_group_id=group.id)
    db_session.commit()
    token = rls_client.post("/api/v1/oauth/token", data={"grant_type": "client_credentials", "client_id": issued.client.client_id,
                                                         "client_secret": issued.secret})
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}

    answer = rls_client.post("/api/v1/checks/verify", headers=headers,
                             json={"account_number": other_account.account_number, "check_number": "7777", "amount": "80.00"})
    assert answer.status_code == 200, answer.text
    assert (answer.json()["result"], answer.json()["issued"]) == ("not_on_positive_pay", None)
