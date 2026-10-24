# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Offboarding a customer (plans/CUSTOMER_OFFBOARDING_PLAN.md): two bank administrators set its
last service day and its people are told; after it, nobody there signs in and files naming its
accounts are refused; 90 days later its data is deleted, with a certificate the bank keeps, unless
it's on legal hold. Other customers and the bank's own records are untouched."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from openclear.bulk_import.fields import RowFieldError
from openclear.domain.account import Account
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.bank_deletion import CustomerDeletion
from openclear.domain.customer import Customer
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.issued_item import IssuedItem
from openclear.domain.notification import Notification, NotificationType
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.services import (account_service, change_request_service, customer_offboarding_service, evidence_service,
                                issued_item_service, security_group_service, user_service)
from openclear.services.customer_offboarding_service import CustomerOffboardingError
from tests.conftest import TenantFactory
from tests.items import _make_customer_with_account


def _person(db_session, tenant, customer, email):
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    return user_service.create_user_with_membership(db_session, tenant.id, email=email, password=TenantFactory.PASSWORD,
                                                    security_group_id=group.id, customer_id=customer.id)


def _schedule(db_session, tenant, users, customer, last_day):
    outcome = change_request_service.request_or_apply(
        db_session, "customer.offboard", {"customer_id": str(customer.id), "last_service_day": last_day.isoformat()},
        tenant_id=tenant.id, user_id=users["admin"].id)
    db_session.commit()
    assert outcome.waiting  # always a second administrator
    change_request_service.decide(db_session, tenant.id, outcome.change.id, approve=True, user_id=users["admin2"].id)
    db_session.commit()


@pytest.fixture
def bank(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="co-bank")
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Admin")
    users["admin2"] = user_service.create_user_with_membership(db_session, tenant.id, email="second.admin@co-bank.example.com",
                                                               password=TenantFactory.PASSWORD, security_group_id=group.id)
    db_session.commit()
    return tenant, users


def test_a_customers_service_ends_and_its_data_is_deleted_ninety_days_later(db_session, bank):
    tenant, users = bank
    leaving, account = _make_customer_with_account(db_session, tenant, "CO-LEAVE")
    staying, other_account = _make_customer_with_account(db_session, tenant, "CO-STAY")
    person = _person(db_session, tenant, leaving, "ap@leaving.example.com")
    issued_item_service.create_issued_item(db_session, tenant.id, issued_item_service.IssuedItemInput(
        account_id=account.id, check_number="501", amount=Decimal("10.00"), payee_name="X", issue_date=date.today()),
        submitted_by_user_id=users["admin"].id)
    ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=account.id, check_number="999",
                                                               presented_amount=Decimal("75.00"), presented_date=date.today()))
    ingest_paid_item(db_session, tenant.id, PaidItemSubmission(account_id=other_account.id, check_number="998",
                                                               presented_amount=Decimal("75.00"), presented_date=date.today()))
    db_session.commit()

    _schedule(db_session, tenant, users, leaving, customer_offboarding_service.bank_today(db_session, tenant.id) + timedelta(days=1))
    db_session.expire_all()
    leaving = db_session.get(Customer, leaving.id)
    assert leaving.service_ends_at is not None and leaving.is_active
    told = db_session.execute(select(Notification).where(Notification.notification_type == NotificationType.SERVICE_ENDING)).scalars().all()
    assert [n.destination for n in told] == ["ap@leaving.example.com"] and "Data export" in told[0].body

    # After the last day: no sign-in, its files refused.
    customer_offboarding_service.sweep(db_session, tenant.id, now=datetime.now(timezone.utc) + timedelta(days=2))
    db_session.expire_all()
    leaving = db_session.get(Customer, leaving.id)
    assert leaving.service_ended_at is not None and not leaving.is_active
    with pytest.raises(RowFieldError, match="Positive pay ended"):
        account_service.get_account_by_number(db_session, tenant.id, account.account_number)
    assert account_service.get_account_by_number(db_session, tenant.id, other_account.account_number) is not None

    # Not yet: 90 days after the end.
    leaving.service_ended_at = datetime.now(timezone.utc) - timedelta(days=89)
    db_session.commit()
    customer_offboarding_service.sweep(db_session, tenant.id)
    assert db_session.get(Customer, leaving.id) is not None
    leaving.service_ended_at = datetime.now(timezone.utc) - timedelta(days=91)
    db_session.commit()
    leaving_id, account_id, person_id = leaving.id, account.id, person.id
    customer_offboarding_service.sweep(db_session, tenant.id)
    db_session.expire_all()

    assert db_session.get(Customer, leaving_id) is None and db_session.get(Account, account_id) is None
    for model, column, value in ((IssuedItem, IssuedItem.account_id, account_id), (ExceptionItem, ExceptionItem.customer_id, leaving_id)):
        assert db_session.execute(select(func.count()).select_from(model).where(column == value)).scalar_one() == 0
    assert db_session.execute(select(func.count()).select_from(TenantMembership).where(TenantMembership.customer_id == leaving_id)).scalar_one() == 0
    assert db_session.get(User, person_id) is None
    assert db_session.get(Customer, staying.id) is not None and db_session.get(Account, other_account.id) is not None
    assert db_session.execute(select(func.count()).select_from(ExceptionItem).where(ExceptionItem.customer_id == staying.id)).scalar_one() == 1

    [record] = customer_offboarding_service.deletions(db_session, tenant.id)
    assert record.customer_number == "CO-LEAVE" and evidence_service.verify(record.certificate)
    certificate = record.certificate["bundle"]
    assert certificate["rows_deleted"]["account"] == 1 and certificate["rows_deleted"]["issued_item"] == 1 and certificate["people_deleted"] == 1
    actions = set(db_session.execute(select(AuditLogEntry.action).where(AuditLogEntry.tenant_id == tenant.id)).scalars())
    assert {"customer.offboard.schedule", "customer.offboard.ended", "customer.offboard.delete"} <= actions  # the bank's log stays


def test_a_hold_stops_the_deletion_and_the_end_can_be_undone(db_session, bank):
    tenant, users = bank
    customer, _account = _make_customer_with_account(db_session, tenant, "CO-HOLD")
    db_session.commit()
    with pytest.raises(change_request_service.ChangeRequestError, match="past"):
        change_request_service.request_or_apply(db_session, "customer.offboard", {"customer_id": str(customer.id),
                                                "last_service_day": (customer_offboarding_service.bank_today(db_session, tenant.id)
                                                                     - timedelta(days=3)).isoformat()},
                                                tenant_id=tenant.id, user_id=users["admin"].id)
        db_session.commit()
        change_request_service.decide(db_session, tenant.id, change_request_service.pending(db_session, tenant.id)[0].id,
                                      approve=True, user_id=users["admin2"].id)
    db_session.rollback()
    for change in change_request_service.pending(db_session, tenant.id):
        change_request_service.withdraw(db_session, tenant.id, change.id, user_id=users["admin"].id)
    db_session.commit()

    _schedule(db_session, tenant, users, customer, customer_offboarding_service.bank_today(db_session, tenant.id))
    customer_offboarding_service.sweep(db_session, tenant.id, now=datetime.now(timezone.utc) + timedelta(days=2))
    # One administrator can put it on hold.
    change_request_service.request_or_apply(db_session, "customer.legal_hold", {"customer_id": str(customer.id), "held": True,
                                            "reason": "Subpoena"}, tenant_id=tenant.id, user_id=users["admin"].id)
    db_session.commit()
    db_session.get(Customer, customer.id).service_ended_at = datetime.now(timezone.utc) - timedelta(days=200)
    db_session.commit()
    customer_offboarding_service.sweep(db_session, tenant.id)
    assert db_session.get(Customer, customer.id) is not None
    with pytest.raises(CustomerOffboardingError, match="legal hold"):
        customer_offboarding_service.check_can_delete(db_session.get(Customer, customer.id))

    # Restoring an ended customer reopens access: a second administrator.
    outcome = change_request_service.request_or_apply(db_session, "customer.offboard", {"customer_id": str(customer.id), "last_service_day": None},
                                                      tenant_id=tenant.id, user_id=users["admin"].id)
    db_session.commit()
    assert outcome.waiting
    change_request_service.decide(db_session, tenant.id, outcome.change.id, approve=True, user_id=users["admin2"].id)
    db_session.commit()
    db_session.expire_all()
    restored = db_session.get(Customer, customer.id)
    assert restored.is_active and restored.service_ended_at is None and restored.service_ends_at is None


def test_the_customer_page_and_the_deleted_list(client, db_session, bank):
    from tests.test_web.test_staff_and_operator import _login

    tenant, users = bank
    customer, _account = _make_customer_with_account(db_session, tenant, "CO-PAGE")
    db_session.add(CustomerDeletion(tenant_id=tenant.id, customer_id=customer.id, customer_number="CO-GONE", customer_name="Gone Co",
                                    completed_at=datetime.now(timezone.utc), certificate={"bundle": {}, "signature": {}},
                                    certificate_sha256="0" * 64))
    db_session.commit()
    _login(client, tenant.slug, users["admin"].email)
    page = client.get(f"/ui/customers/{customer.id}")
    assert page.status_code == 200 and "End positive pay" in page.text
    listed = client.get("/ui/customers/deleted")
    assert listed.status_code == 200 and "Gone Co" in listed.text
    assert client.get(f"/ui/customers/deleted/{db_session.execute(select(CustomerDeletion.id)).scalar_one()}/certificate").status_code == 200


def test_the_last_service_day_is_a_day_at_the_bank_not_on_the_server(db_session, bank):
    """At 00:30 UTC it's still the evening before in Los Angeles: "today" is the bank's day, and
    its last service day ends at midnight there."""
    tenant, _users = bank
    tenant.time_zone = "America/Los_Angeles"
    db_session.commit()
    evening = datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc)
    assert customer_offboarding_service.bank_today(db_session, tenant.id, now=evening) == date(2026, 10, 23)
    assert customer_offboarding_service._end_of_day(db_session, tenant.id, date(2026, 10, 23)) == datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)
