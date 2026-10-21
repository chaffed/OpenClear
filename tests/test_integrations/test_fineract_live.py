# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The Fineract adapter against a real Apache Fineract (SDK_PLAN.md P4's sandbox core): seed
it, sync, present its checks, return one, and see the withdrawal undone in Fineract itself.
Skipped unless OPENCLEAR_TEST_FINERACT_URL (default https://localhost:8443/...) answers."""

import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from openclear.domain.account import Account
from openclear.domain.decision import DecisionOutcome
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.paid_item import PaidItem
from openclear.services import api_client_service, issued_item_service, security_group_service
from tests.items import _decide

from openclear_fineract import Adapter, Fineract, State  # noqa: E402
from openclear_fineract.demo import seed  # noqa: E402
from openclear_sdk import OpenClear  # noqa: E402

URL = os.environ.get("OPENCLEAR_TEST_FINERACT_URL", "https://localhost:8443/fineract-provider/api/v1")


def _fineract() -> Fineract | None:
    client = httpx.Client(verify=False, timeout=60)
    fineract = Fineract(URL, username="mifos", password="password", http_client=client)
    try:
        fineract._request("GET", "/offices")
        return fineract
    except Exception:  # noqa: BLE001
        return None


FINERACT = _fineract()
pytestmark = pytest.mark.skipif(FINERACT is None, reason="needs a running Fineract (sandbox/README.md)")


def test_the_adapter_against_a_real_fineract(client, db_session, tenant_factory, tmp_path):
    external = f"CIF-{uuid.uuid4().hex[:8].upper()}"
    core = seed(FINERACT, external_id=external, checks=(("2001", 125.0), ("2002", 640.0)))
    tenant, _account, users = tenant_factory.make(slug="fineract-live")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    issued = api_client_service.create_client(db_session, tenant.id, name="Fineract adapter", security_group_id=group.id)
    db_session.commit()
    adapter = Adapter(OpenClear("http://testserver", client_id=issued.client.client_id, client_secret=issued.secret, http_client=client),
                      FINERACT, State(tmp_path / "state.sqlite"))

    adapter.sync()
    account = db_session.execute(select(Account).where(Account.tenant_id == tenant.id, Account.account_number == core["account_no"])).scalar_one()
    issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number="2001", amount=Decimal("125.00"), payee_name="Vendor", issue_date=date.today() - timedelta(days=5),
    ), submitted_by_user_id=users["preparer"].id)
    db_session.commit()

    presented = adapter.presented()
    assert presented.presented >= 2
    # Check 2002 on this account was never issued. (Fineract may hold other sandbox accounts too.)
    exception = db_session.execute(
        select(ExceptionItem).join(PaidItem, PaidItem.id == ExceptionItem.source_item_id)
        .where(ExceptionItem.tenant_id == tenant.id, PaidItem.account_id == account.id, PaidItem.check_number == "2002")
    ).scalar_one()
    assert exception.exception_types == "not_in_file"
    _decide(db_session, tenant, users, exception, DecisionOutcome.RETURN)
    assert adapter.decisions().returned == 1

    reversed_ = {t["paymentDetailData"]["checkNumber"]: t.get("reversed") for t in FINERACT.savings_transactions(core["account_id"])
                 if (t.get("paymentDetailData") or {}).get("checkNumber")}
    assert reversed_ == {"2001": False, "2002": True}  # the returned check's withdrawal is undone in Fineract
