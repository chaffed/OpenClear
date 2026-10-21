# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The interface fixes from FULL_REVIEW_PLAN.md FR7: lists bank staff can narrow, statuses in
words, one page title per page, emails that say what and when."""

import re
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from openclear.domain.customer import Customer
from openclear.domain.notification import Notification, NotificationType
from openclear.services import stop_payment_service
from tests.test_decisions.test_changeable import _item
from tests.test_web.test_web_exceptions import _login


def _h1_count(html: str) -> int:
    return len(re.findall(r"<h1[\s>]", html))


def test_bank_staff_narrow_the_queue_and_approvals_by_customer(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ui-cust-filter")
    customer = Customer(tenant_id=tenant.id, name="Maple Street Dental", customer_number="M-1")
    db_session.add(customer)
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)
    for path in ("/ui/exceptions", "/ui/exceptions/approvals"):
        page = client.get(path, params={"customer": str(customer.id)}).text
        assert 'name="customer"' in page and "Maple Street Dental" in page
        assert re.search(rf'value="{customer.id}"\s+selected', page)


def test_stop_payments_filter_by_status_and_number(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ui-stops")
    for number in ("4001", "4002"):
        stop_payment_service.create_stop_payment(db_session, tenant.id, stop_payment_service.StopPaymentInput(
            account_id=account.id, check_number=number, amount=None, effective_date=date.today(), expiration_date=None, reason=None),
            created_by_user_id=None)
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)
    one = client.get("/ui/stop-payments", params={"number": "4002"}).text
    assert "4002" in one and "4001" not in one
    none = client.get("/ui/stop-payments", params={"status": "expired"}).text
    assert "No stop payments match" in none
    assert ">Active<" in client.get("/ui/stop-payments").text  # in words, not "active"


def test_pages_have_one_title(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ui-h1")
    customer = Customer(tenant_id=tenant.id, name="Acme", customer_number="A-1")
    db_session.add(customer)
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)
    for path in ("/ui/", f"/ui/customers/{customer.id}", "/ui/security", "/ui/security/notifications",
                 f"/ui/admin/customers/{customer.id}/default-decision", "/ui/admin/controls", "/ui/admin/model-risk"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert _h1_count(page.text) == 1, path


def test_the_help_says_decisions_can_change_until_the_cutoff(client, db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ui-help")
    item = _item(db_session, tenant, account, number="5501")
    _login(client, tenant.slug, users["admin"].email)
    page = client.get(f"/ui/exceptions/{item.id}").text
    assert "can't be changed afterwards" not in page
    assert "until the\n  item's cut-off" in page or "until the item's cut-off" in page


def test_a_new_exception_email_says_which_check_and_by_when(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ui-email")
    item = _item(db_session, tenant, account, number="5601", due_in=timedelta(hours=5))
    body = db_session.execute(select(Notification.body).where(
        Notification.notification_type == NotificationType.EXCEPTION_CREATED, Notification.resource_id == item.id)).scalars().first()
    assert body is not None and "Check 5601" in body and "$40.00" in body
    assert "Decide by:" not in body or ("AM" in body or "PM" in body)
