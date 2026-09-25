# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from datetime import date
from decimal import Decimal

from openclear.domain.notification import Notification, NotificationChannel, NotificationStatus, NotificationType
from openclear.networks.check.ingestion import PaidItemSubmission, ingest_paid_item
from openclear.repositories.exception_repo import ExceptionRepository
from openclear.services import customer_service, decision_service, notification_service, user_service
from openclear.services.customer_service import CustomerInput
from openclear.services.decision_service import DecisionOutcome


def _notifications(db_session, recipient_user_id=None, notification_type=None):
    q = db_session.query(Notification)
    if recipient_user_id is not None:
        q = q.filter(Notification.recipient_user_id == recipient_user_id)
    if notification_type is not None:
        q = q.filter(Notification.notification_type == notification_type)
    return q.all()


def _make_mismatched_paid_item(db_session, tenant, account, *, check_number="9001"):
    """A presented amount with no matching issued item -> a fresh, always-reliable
    exception, same trick used elsewhere in this session's tests."""
    return ingest_paid_item(
        db_session, tenant.id,
        PaidItemSubmission(account_id=account.id, check_number=check_number, presented_amount=Decimal("999.00"), presented_date=date(2026, 1, 15)),
    )


def test_exception_created_notifies_users_with_recommend_permission(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="notif-exception-created")
    _make_mismatched_paid_item(db_session, tenant, account)
    db_session.commit()

    preparer_emails = _notifications(db_session, users["preparer"].id, NotificationType.EXCEPTION_CREATED)
    admin_emails = _notifications(db_session, users["admin"].id, NotificationType.EXCEPTION_CREATED)
    viewer_emails = _notifications(db_session, users["viewer"].id, NotificationType.EXCEPTION_CREATED)

    assert len(preparer_emails) == 1
    assert preparer_emails[0].channel == NotificationChannel.EMAIL
    assert preparer_emails[0].destination == users["preparer"].email
    assert preparer_emails[0].status == NotificationStatus.PENDING
    assert len(admin_emails) == 1  # Admin holds every permission, including exception:recommend
    assert viewer_emails == []  # Viewer has no exception:recommend


def test_exception_created_customer_scoped_visibility(db_session, tenant_factory):
    from openclear.services import account_service, security_group_service

    tenant, house_account, users = tenant_factory.make(slug="notif-exception-customer-scope")
    customer_a = customer_service.create_customer(db_session, tenant.id, CustomerInput(customer_number="A", name="Customer A"))
    customer_b = customer_service.create_customer(db_session, tenant.id, CustomerInput(customer_number="B", name="Customer B"))
    account_a = account_service.create_account(
        db_session, tenant.id, account_service.AccountInput(account_number="A-1", name="A", customer_id=customer_a.id)
    )
    preparer_group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    staff_a = user_service.create_user_with_membership(
        db_session, tenant.id, email="staff-a@notif-cust.example.com", password="test-password-123",
        security_group_id=preparer_group.id, customer_id=customer_a.id,
    )
    staff_b = user_service.create_user_with_membership(
        db_session, tenant.id, email="staff-b@notif-cust.example.com", password="test-password-123",
        security_group_id=preparer_group.id, customer_id=customer_b.id,
    )
    db_session.commit()

    ingest_paid_item(
        db_session, tenant.id,
        PaidItemSubmission(account_id=account_a.id, check_number="7001", presented_amount=Decimal("50.00"), presented_date=date(2026, 1, 1)),
    )
    db_session.commit()

    assert len(_notifications(db_session, staff_a.id, NotificationType.EXCEPTION_CREATED)) == 1
    assert _notifications(db_session, staff_b.id, NotificationType.EXCEPTION_CREATED) == []
    # tenant-wide preparer sees every customer's exceptions too
    assert len(_notifications(db_session, users["preparer"].id, NotificationType.EXCEPTION_CREATED)) == 1


def test_recommendation_awaiting_approval_excludes_the_maker(db_session, tenant_factory):
    import uuid

    from openclear.db.tenancy import TenantContext

    tenant, account, users = tenant_factory.make(slug="notif-recommendation")
    _make_mismatched_paid_item(db_session, tenant, account)
    db_session.commit()
    exception = ExceptionRepository(db_session, tenant.id).list()[0]

    # submit_recommendation only reads ctx.user_id/ctx.customer_id -- authorization
    # itself is enforced at the route layer (require_web_permission), not in this
    # service function, so the other TenantContext fields are irrelevant here.
    preparer_ctx = TenantContext(
        tenant_id=tenant.id, user_id=users["preparer"].id, security_group_id=uuid.uuid4(),
        permissions=frozenset(), tenant_slug=tenant.slug, tenant_name=tenant.name, accent_color=None,
        has_logo=False, has_favicon=False, customer_id=None, customer_name=None,
    )
    result = decision_service.submit_recommendation(
        db_session, tenant.id, exception.id, preparer_ctx, outcome=DecisionOutcome.PAY, reason_code="ok", notes=None,
    )
    db_session.commit()
    assert result.error is None

    approver_notifs = _notifications(db_session, users["approver"].id, NotificationType.RECOMMENDATION_AWAITING_APPROVAL)
    preparer_notifs = _notifications(db_session, users["preparer"].id, NotificationType.RECOMMENDATION_AWAITING_APPROVAL)
    assert len(approver_notifs) == 1
    assert preparer_notifs == []  # the maker doesn't get told about their own recommendation


def test_account_locked_notification_ignores_preference_override(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="notif-lockout-override")
    user = users["admin"]
    # Explicitly turn email off for ACCOUNT_LOCKED -- must be ignored, this type is
    # never skippable for email.
    notification_service.set_preference(db_session, user.id, NotificationType.ACCOUNT_LOCKED, email_enabled=False, sms_enabled=False)
    db_session.commit()

    notification_service.notify_account_locked(db_session, user)
    db_session.commit()

    notifs = _notifications(db_session, user.id, NotificationType.ACCOUNT_LOCKED)
    assert len(notifs) == 1
    assert notifs[0].channel == NotificationChannel.EMAIL


def test_account_unlocked_notification_respects_preference(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="notif-unlock-pref")
    user = users["admin"]
    notification_service.set_preference(db_session, user.id, NotificationType.ACCOUNT_UNLOCKED, email_enabled=False, sms_enabled=False)
    db_session.commit()

    notification_service.notify_account_unlocked(db_session, user)
    db_session.commit()

    assert _notifications(db_session, user.id, NotificationType.ACCOUNT_UNLOCKED) == []


def test_unlock_user_service_triggers_notification(db_session, tenant_factory):
    from openclear.repositories.tenant_membership_repo import TenantMembershipRepository

    tenant, _account, users = tenant_factory.make(slug="notif-unlock-service")
    user = users["admin"]
    user.failed_login_attempts = 5
    from datetime import datetime, timedelta, timezone

    user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
    db_session.commit()

    membership = TenantMembershipRepository(db_session, tenant.id).list(user_id=user.id)[0]
    user_service.unlock_user(db_session, tenant.id, membership.id)
    db_session.commit()

    assert len(_notifications(db_session, user.id, NotificationType.ACCOUNT_UNLOCKED)) == 1


def test_sms_only_queued_when_phone_is_set(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="notif-sms-phone-gate")
    user = users["admin"]
    notification_service.set_preference(db_session, user.id, NotificationType.ACCOUNT_UNLOCKED, email_enabled=True, sms_enabled=True)
    db_session.commit()

    # No phone set -- SMS must not be queued even though sms_enabled=True
    notification_service.notify_account_unlocked(db_session, user)
    db_session.commit()
    notifs = _notifications(db_session, user.id, NotificationType.ACCOUNT_UNLOCKED)
    assert [n.channel for n in notifs] == [NotificationChannel.EMAIL]

    notification_service.set_phone(db_session, user, "+15551234567")
    db_session.commit()
    notification_service.notify_account_unlocked(db_session, user)
    db_session.commit()
    notifs = _notifications(db_session, user.id, NotificationType.ACCOUNT_UNLOCKED)
    channels = sorted(n.channel.value for n in notifs)
    assert channels == ["email", "email", "sms"]


def test_preference_defaults_when_no_row_exists(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="notif-pref-defaults")
    email_enabled, sms_enabled = notification_service._effective_preference(
        db_session, users["admin"].id, NotificationType.EXCEPTION_CREATED
    )
    assert email_enabled is True
    assert sms_enabled is False


def test_set_preference_persists_and_is_reused(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="notif-pref-persist")
    notification_service.set_preference(
        db_session, users["admin"].id, NotificationType.EXCEPTION_CREATED, email_enabled=False, sms_enabled=True
    )
    db_session.commit()

    prefs = notification_service.get_preferences(db_session, users["admin"].id)
    assert prefs[NotificationType.EXCEPTION_CREATED].email_enabled is False
    assert prefs[NotificationType.EXCEPTION_CREATED].sms_enabled is True

    # Setting again updates the same row rather than creating a second one
    notification_service.set_preference(
        db_session, users["admin"].id, NotificationType.EXCEPTION_CREATED, email_enabled=True, sms_enabled=False
    )
    db_session.commit()
    prefs = notification_service.get_preferences(db_session, users["admin"].id)
    assert len(prefs) == 1
    assert prefs[NotificationType.EXCEPTION_CREATED].email_enabled is True
