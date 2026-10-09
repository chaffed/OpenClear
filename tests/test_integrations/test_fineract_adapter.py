# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The Apache Fineract reference adapter (integrations/fineract, SDK_PLAN.md P3) end to end:
a simulated Fineract REST API on one side, the real OpenClear app (through the SDK) on the
other."""

import json
from datetime import date
from decimal import Decimal

import httpx
from sqlalchemy import select

from openclear.domain.account import Account
from openclear.domain.customer import Customer
from openclear.domain.decision import DecisionOutcome
from openclear.domain.exception_item import ExceptionItem
from openclear.services import api_client_service, issued_item_service, security_group_service
from tests.test_ml.test_per_customer_ml import _decide

from openclear_fineract import Adapter, Fineract, State  # noqa: E402 -- from integrations/fineract/src (conftest.py)
from openclear_sdk import OpenClear  # noqa: E402


class FakeFineract:
    """Just enough of Fineract 1.x: paged clients and savings accounts, an account's
    transactions, and undo."""

    def __init__(self) -> None:
        self.clients = [{"id": 1, "accountNo": "000000001", "externalId": "CIF-77", "displayName": "Acme Corp", "active": True}]
        self.accounts = [{"id": 10, "accountNo": "SAV-0001", "clientId": 1, "savingsProductName": "Business Checking"}]
        self.transactions = {10: [
            {"id": 501, "transactionType": {"withdrawal": True}, "amount": 250.00, "date": [2026, 10, 7], "reversed": False,
             "paymentDetailData": {"paymentType": {"name": "Check"}, "checkNumber": "1001"}},
            {"id": 502, "transactionType": {"withdrawal": True}, "amount": 999.00, "date": [2026, 10, 7], "reversed": False,
             "paymentDetailData": {"paymentType": {"name": "Check"}, "checkNumber": "1002"}},
            {"id": 503, "transactionType": {"withdrawal": True}, "amount": 40.00, "date": [2026, 10, 7], "reversed": False,
             "paymentDetailData": {"paymentType": {"name": "Cash"}}},
            {"id": 504, "transactionType": {"deposit": True}, "amount": 5000.00, "date": [2026, 10, 1], "reversed": False},
        ]}
        self.undone: list[tuple[int, int]] = []
        self.seen_tenants: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen_tenants.add(request.headers.get("Fineract-Platform-TenantId"))
        path = request.url.path.removeprefix("/fineract-provider/api/v1")
        if path == "/clients":
            return httpx.Response(200, json={"totalFilteredRecords": len(self.clients), "pageItems": self.clients})
        if path == "/savingsaccounts":
            return httpx.Response(200, json={"totalFilteredRecords": len(self.accounts), "pageItems": self.accounts})
        if path.startswith("/savingsaccounts/") and request.method == "GET":
            account_id = int(path.split("/")[2])
            return httpx.Response(200, json={"id": account_id, "transactions": self.transactions.get(account_id, [])})
        if "/transactions/" in path and request.url.params.get("command") == "undo":
            parts = path.split("/")
            self.undone.append((int(parts[2]), int(parts[4])))
            return httpx.Response(200, json={"resourceId": int(parts[4])})
        return httpx.Response(404, json={"error": path})


def test_sync_presented_checks_and_returns_undone_in_fineract(client, db_session, tenant_factory, tmp_path):
    tenant, _account, users = tenant_factory.make(slug="fineract-e2e")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    issued = api_client_service.create_client(db_session, tenant.id, name="Fineract adapter", security_group_id=group.id)
    db_session.commit()
    fake = FakeFineract()
    adapter = Adapter(
        OpenClear("http://testserver", client_id=issued.client.client_id, client_secret=issued.secret, http_client=client),
        Fineract("https://fineract.example/fineract-provider/api/v1", username="mifos", password="password",
                 http_client=httpx.Client(transport=httpx.MockTransport(fake.handler))),
        State(tmp_path / "state.sqlite"),
    )

    synced = adapter.sync()
    assert (synced.customers, synced.accounts) == (1, 1) and fake.seen_tenants == {"default"}
    account = db_session.execute(select(Account).where(Account.tenant_id == tenant.id, Account.account_number == "SAV-0001")).scalar_one()
    customer = db_session.execute(select(Customer).where(Customer.tenant_id == tenant.id, Customer.customer_number == "CIF-77")).scalar_one()
    assert account.customer_id == customer.id and account.external_account_id == "10"

    # Check 1001 was issued for that amount; 1002 never was.
    issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number="1001", amount=Decimal("250.00"), payee_name="Vendor", issue_date=date(2026, 10, 1),
    ), submitted_by_user_id=users["preparer"].id)
    db_session.commit()

    presented = adapter.presented()
    assert presented.presented == 2 and presented.skipped == []  # the cash withdrawal and the deposit aren't checks
    assert adapter.presented().presented == 0  # already sent
    [exception] = db_session.execute(select(ExceptionItem).where(ExceptionItem.tenant_id == tenant.id)).scalars().all()
    assert exception.exception_types == "not_in_file"

    _decide(db_session, tenant, users, exception, DecisionOutcome.RETURN)
    result = adapter.decisions()
    assert (result.returned, fake.undone) == (1, [(10, 502)])
    assert adapter.decisions().returned == 0  # acknowledged as consumer "fineract"
    assert json.dumps(fake.undone) == "[[10, 502]]"
