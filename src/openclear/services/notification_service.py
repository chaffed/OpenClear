# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Queues Notification rows -- never sends anything itself, so calling this from a
request path is always cheap and safe (a plain DB insert). workers/tasks.py::
notification_dispatch_job is what actually calls out to a provider, on its own
background schedule, using notifications/email and notifications/sms's provider
abstraction. Call sites: networks/check/ingestion.py + networks/ach/ingestion.py
(notify_exception_created), services/decision_service.py (notify_recommendation_
awaiting_approval), auth/login_service.py + services/user_service.py (notify_account_
locked/unlocked) -- each called right after its triggering mutation succeeds, same
"right after the action, before commit" convention services/audit_log_service.py's
record_action() already established."""

import contextlib
import contextvars
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from openclear.config import get_settings
from openclear.domain.decision import Decision, DecisionSource
from openclear.domain.exception_item import ExceptionItem
from openclear.domain.notification import Notification, NotificationChannel, NotificationPreference, NotificationType
from openclear.domain.security_group import SecurityGroup
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.web.templates import templates

# ACCOUNT_LOCKED's, PASSWORD_CHANGED's and INACTIVITY_WARNING's emails are not skippable via preference -- the
# user needs to know regardless of what they've configured, same "security mail isn't
# optional" reasoning most apps apply to account-security notices. Every other type (and
# SMS for every type, including these) stays fully preference-controlled.
_ALWAYS_EMAIL_TYPES = frozenset({NotificationType.ACCOUNT_LOCKED, NotificationType.PASSWORD_CHANGED, NotificationType.INACTIVITY_WARNING,
                                 NotificationType.PASSWORD_RESET_LINK, NotificationType.SECURITY_KEY_CHANGED,
                                 NotificationType.NEW_DEVICE_SIGN_IN, NotificationType.MALWARE_DETECTED})

_EMAIL_SUBJECTS: dict[NotificationType, str] = {
    NotificationType.EXCEPTION_CREATED: "New exception ready for review",
    NotificationType.RECOMMENDATION_AWAITING_APPROVAL: "A recommendation is awaiting your approval",
    NotificationType.ACCOUNT_LOCKED: "Your OpenClear account has been locked",
    NotificationType.ACCOUNT_UNLOCKED: "Your OpenClear account has been unlocked",
    NotificationType.EXCEPTION_AUTO_DECIDED: "An exception was auto-decided",
    NotificationType.PASSWORD_CHANGED: "Your OpenClear password was changed",
    NotificationType.DEADLINE_REMINDER: "Items to decide before the cutoff",
    NotificationType.INVITATION: "You're invited to OpenClear",
    NotificationType.PASSWORD_RESET_LINK: "Choose a new OpenClear password",
    NotificationType.SECURITY_KEY_CHANGED: "A security key was changed on your OpenClear sign-in",
    NotificationType.NEW_DEVICE_SIGN_IN: "A new sign-in to your OpenClear account",
    NotificationType.EMAIL_CHANGE_CONFIRM: "Confirm your new OpenClear email address",
    NotificationType.EMAIL_CHANGED: "The email address on your OpenClear sign-in changed",
    NotificationType.MALWARE_DETECTED: "Malware was found in a file uploaded to OpenClear",
    NotificationType.CUSTOMER_USER_DECIDED: "Your bank decided on a new person",
    NotificationType.ATTESTATION_REQUESTED: "Your bank asks you to attest your responsibilities",
    NotificationType.INACTIVITY_WARNING: "Sign in to keep your OpenClear access",
    NotificationType.CHANGE_REQUESTED: "A change is waiting for your approval",
    NotificationType.CHANGE_DECIDED: "Your change was decided",
    NotificationType.APPROVER_GONE: "Nobody else can approve some changes",
}


def _recipients_for_permission(
    session: Session, tenant_id: uuid.UUID, permission: str, *, customer_id: uuid.UUID | None,
    account_id: uuid.UUID | None = None,
) -> list[User]:
    """Every active membership in scope whose security group holds `permission` --
    tenant-wide staff (membership.customer_id IS NULL) always included, customer-scoped
    staff included only when `customer_id` matches, mirroring the exact visibility rule
    used everywhere else in this app (a customer-scoped session sees only its own
    customer's data; a tenant-wide session sees everything). Permission membership is
    checked in Python against the group's raw `permissions` list, same as
    auth/deps.py::decode_and_build_context -- this does NOT apply
    CUSTOMER_SCOPE_MASKED_PERMISSIONS's masking, so it must only ever be called with a
    permission that's never masked (true of exception:recommend/exception:decide, the
    only two callers today).

    A customer's person limited to some accounts (db/account_scope.py) is included only
    when `account_id`, the account the news is about, is one of theirs."""
    scope_filter = (
        TenantMembership.customer_id.is_(None)
        if customer_id is None
        else or_(TenantMembership.customer_id == customer_id, TenantMembership.customer_id.is_(None))
    )
    rows = session.execute(
        select(TenantMembership, SecurityGroup, User)
        .join(SecurityGroup, SecurityGroup.id == TenantMembership.security_group_id)
        .join(User, User.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == tenant_id, TenantMembership.is_active.is_(True), scope_filter)
    ).all()

    limited = _limited_memberships(session, [m for m, _g, _u in rows])
    recipients: dict[uuid.UUID, User] = {}
    for membership, group, user in rows:
        if membership.id in limited and account_id not in limited[membership.id]:
            continue
        if permission in group.permissions:
            recipients[user.id] = user
    return list(recipients.values())


def _limited_memberships(session: Session, memberships: list[TenantMembership]) -> dict[uuid.UUID, set[uuid.UUID]]:
    """The memberships among these that see only some accounts, and those accounts."""
    from openclear.domain.tenant_membership import MembershipAccount

    ids = [m.id for m in memberships if m.customer_id is not None and not m.all_accounts]
    limited: dict[uuid.UUID, set[uuid.UUID]] = {i: set() for i in ids}
    if ids:
        for membership_id, account in session.execute(
                select(MembershipAccount.membership_id, MembershipAccount.account_id).where(MembershipAccount.membership_id.in_(ids))):
            limited[membership_id].add(account)
    return limited


def exception_account_id(session: Session, exception_item: ExceptionItem) -> uuid.UUID | None:
    """The account an exception is on, from its paid item or ACH transaction."""
    from openclear.domain.ach_transaction import AchTransaction
    from openclear.domain.paid_item import PaidItem

    for model in (PaidItem, AchTransaction):
        account = session.execute(select(model.account_id).where(model.id == exception_item.source_item_id)).scalar_one_or_none()
        if account is not None:
            return account
    return None


def _effective_preference(session: Session, user_id: uuid.UUID, notification_type: NotificationType) -> tuple[bool, bool]:
    """Absence of a NotificationPreference row is not "no preference" -- it's the
    type's own hardcoded default (email on, SMS off), so most users never need a row at
    all. Returns (email_enabled, sms_enabled)."""
    pref = session.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id, NotificationPreference.notification_type == notification_type
        )
    ).scalar_one_or_none()

    if notification_type in _ALWAYS_EMAIL_TYPES:
        email_enabled = True
    else:
        email_enabled = pref.email_enabled if pref is not None else True
    sms_enabled = pref.sms_enabled if pref is not None else False
    return email_enabled, sms_enabled


def _absolute_url(path: str) -> str:
    """Best-effort link back into the app for an email body. No Request object is
    available here (these functions run from request paths, the auto-import scanner, and
    the login flow alike) to build one the way web/routers/auth.py::_redirect_uri does,
    so this only ever uses the explicitly configured public_base_url, falling back to a
    plain relative path (still useful as text, just not clickable from outside the app)
    when it's unset."""
    settings = get_settings()
    if settings.public_base_url:
        return f"{settings.public_base_url.rstrip('/')}{path}"
    return path


def _render_body(
    notification_type: NotificationType, channel: NotificationChannel, context: dict[str, Any], template: str | None = None
) -> str:
    template_name = f"notifications/{template or notification_type.value}_{channel.value}.txt"
    return templates.env.get_template(template_name).render(**context)


def _queue(
    session: Session,
    *,
    tenant_id: uuid.UUID | None,
    recipient: User,
    notification_type: NotificationType,
    template_context: dict[str, Any],
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    template: str | None = None,
    subject: str | None = None,
) -> None:
    """`template` and `subject` override the type's own, for a variant of a type (the
    bulk upload summary) that the same notification preference governs."""
    email_enabled, sms_enabled = _effective_preference(session, recipient.id, notification_type)

    if email_enabled:
        session.add(
            Notification(
                tenant_id=tenant_id,
                recipient_user_id=recipient.id,
                notification_type=notification_type,
                channel=NotificationChannel.EMAIL,
                destination=recipient.email,
                subject=subject or _EMAIL_SUBJECTS[notification_type],
                body=_render_body(notification_type, NotificationChannel.EMAIL, template_context, template),
                resource_type=resource_type,
                resource_id=resource_id,
            )
        )
    if sms_enabled and recipient.phone:
        session.add(
            Notification(
                tenant_id=tenant_id,
                recipient_user_id=recipient.id,
                notification_type=notification_type,
                channel=NotificationChannel.SMS,
                destination=recipient.phone,
                subject=None,
                body=_render_body(notification_type, NotificationChannel.SMS, template_context, template),
                resource_type=resource_type,
                resource_id=resource_id,
            )
        )
    session.flush()


_summarizing_exceptions: contextvars.ContextVar[bool] = contextvars.ContextVar("summarizing_exceptions", default=False)


@contextlib.contextmanager
def exceptions_summarized():
    """Inside this block notify_exception_created queues nothing: a bulk import sends one
    summary per upload instead (notify_exceptions_created_summary; SCALE_PLAN.md D5)."""
    token = _summarizing_exceptions.set(True)
    try:
        yield
    finally:
        _summarizing_exceptions.reset(token)


def notify_exceptions_created_summary(
    session: Session, tenant_id: uuid.UUID, counts_by_customer: dict[str, int], *, source_label: str
) -> None:
    """One EXCEPTION_CREATED notification per recipient for a whole bulk upload.
    `counts_by_customer` maps "customer id:account id" (the customer's "" for none; uploads
    from before 1.14 have no account) to how many new exceptions it got; each recipient's
    count covers only the customers and accounts they can see."""
    totals: dict[uuid.UUID, tuple[User, int]] = {}
    for key, count in counts_by_customer.items():
        if not count:
            continue
        customer_key, _, account_key = key.partition(":")
        customer_id = uuid.UUID(customer_key) if customer_key else None
        account_id = uuid.UUID(account_key) if account_key else None
        for recipient in _recipients_for_permission(session, tenant_id, "exception:recommend", customer_id=customer_id,
                                                    account_id=account_id):
            previous = totals.get(recipient.id, (recipient, 0))[1]
            totals[recipient.id] = (recipient, previous + count)
    for recipient, count in totals.values():
        _queue(
            session,
            tenant_id=tenant_id,
            recipient=recipient,
            notification_type=NotificationType.EXCEPTION_CREATED,
            template_context={"count": count, "source_label": source_label, "queue_url": _absolute_url("/ui/exceptions")},
            template="exceptions_created_summary",
            subject=f"{count} new exception{'s' if count != 1 else ''} ready for review",
        )


def notify_exception_created(session: Session, exception_item: ExceptionItem) -> None:
    if _summarizing_exceptions.get():
        return
    recipients = _recipients_for_permission(
        session, exception_item.tenant_id, "exception:recommend", customer_id=exception_item.customer_id,
        account_id=exception_account_id(session, exception_item),
    )
    context = {
        "network_code": exception_item.network_code,
        "exception_types": exception_item.exception_types.replace(",", ", "),
        "exception_id": str(exception_item.id),
        "exception_url": _absolute_url(f"/ui/exceptions/{exception_item.id}"),
    }
    for recipient in recipients:
        _queue(
            session,
            tenant_id=exception_item.tenant_id,
            recipient=recipient,
            notification_type=NotificationType.EXCEPTION_CREATED,
            template_context=context,
            resource_type="exception_item",
            resource_id=exception_item.id,
        )


def notify_recommendation_awaiting_approval(session: Session, exception_item: ExceptionItem) -> None:
    recipients = _recipients_for_permission(
        session, exception_item.tenant_id, "exception:decide", customer_id=exception_item.customer_id,
        account_id=exception_account_id(session, exception_item),
    )
    context = {
        "network_code": exception_item.network_code,
        "exception_id": str(exception_item.id),
        "recommended_outcome": exception_item.recommended_outcome,
        "exception_url": _absolute_url(f"/ui/exceptions/{exception_item.id}"),
    }
    for recipient in recipients:
        # The maker never needs to be told their own just-submitted recommendation is
        # awaiting approval -- mirrors decision_service.decide()'s own maker-cannot-
        # approve-own-recommendation enforcement.
        if recipient.id == exception_item.recommended_by_user_id:
            continue
        _queue(
            session,
            tenant_id=exception_item.tenant_id,
            recipient=recipient,
            notification_type=NotificationType.RECOMMENDATION_AWAITING_APPROVAL,
            template_context=context,
            resource_type="exception_item",
            resource_id=exception_item.id,
        )


def notify_exception_auto_decided(session: Session, exception_item: ExceptionItem, decision: Decision) -> None:
    """Sent when services/auto_disposition_service.py finalizes an exception with no
    human decider -- recipients are the same people who'd normally have finalized it
    themselves (exception:decide), so no one is surprised to see it already closed."""
    recipients = _recipients_for_permission(
        session, exception_item.tenant_id, "exception:decide", customer_id=exception_item.customer_id,
        account_id=exception_account_id(session, exception_item),
    )
    context = {
        "network_code": exception_item.network_code,
        "exception_id": str(exception_item.id),
        "outcome": decision.outcome.value,
        "reason_code": decision.reason_code,
        "is_ml": decision.source == DecisionSource.AUTO_ML,
        "exception_url": _absolute_url(f"/ui/exceptions/{exception_item.id}"),
    }
    for recipient in recipients:
        _queue(
            session,
            tenant_id=exception_item.tenant_id,
            recipient=recipient,
            notification_type=NotificationType.EXCEPTION_AUTO_DECIDED,
            template_context=context,
            resource_type="exception_item",
            resource_id=exception_item.id,
        )


def notify_account_locked(session: Session, user: User, *, minutes: int | None = 15) -> None:
    """`minutes` None: locked until an administrator unlocks it (the bank's choice)."""
    _queue(
        session,
        tenant_id=None,
        recipient=user,
        notification_type=NotificationType.ACCOUNT_LOCKED,
        template_context={"minutes": minutes},
        resource_type="user",
        resource_id=user.id,
    )


def notify_account_unlocked(session: Session, user: User) -> None:
    _queue(
        session,
        tenant_id=None,
        recipient=user,
        notification_type=NotificationType.ACCOUNT_UNLOCKED,
        template_context={},
        resource_type="user",
        resource_id=user.id,
    )


def notify_password_changed(session: Session, user: User, *, reset_by_admin: bool) -> None:
    _queue(
        session,
        tenant_id=None,
        recipient=user,
        notification_type=NotificationType.PASSWORD_CHANGED,
        template_context={"reset_by_admin": reset_by_admin},
        resource_type="user",
        resource_id=user.id,
    )


def email_configured() -> bool:
    """Whether email can actually be sent: a message that can't be delivered only fails later,
    in the worker, so anything that relies on email (a reset link) checks this first."""
    settings = get_settings()
    return settings.email_provider != "smtp" or bool(settings.smtp_host)


def notify_password_reset_link(session: Session, user: User, *, link: str, hours: int, bank_name: str) -> None:
    session.add(Notification(
        tenant_id=None, recipient_user_id=user.id, notification_type=NotificationType.PASSWORD_RESET_LINK,
        channel=NotificationChannel.EMAIL, destination=user.email,
        subject=_EMAIL_SUBJECTS[NotificationType.PASSWORD_RESET_LINK],
        body=_render_body(NotificationType.PASSWORD_RESET_LINK, NotificationChannel.EMAIL,
                          {"link": link, "hours": hours, "bank_name": bank_name}),
        resource_type="user", resource_id=user.id,
    ))
    session.flush()


def notify_security_key_changed(session: Session, user: User, *, added: bool, nickname: str | None, bank_name: str,
                                reset_by_admin: bool = False) -> None:
    _queue(
        session,
        tenant_id=None,
        recipient=user,
        notification_type=NotificationType.SECURITY_KEY_CHANGED,
        template_context={"added": added, "nickname": nickname, "bank_name": bank_name, "reset_by_admin": reset_by_admin},
        resource_type="user",
        resource_id=user.id,
    )


def notify_new_device(session: Session, user: User, *, browser: str, address: str, at, bank_name: str) -> None:
    _queue(
        session, tenant_id=None, recipient=user, notification_type=NotificationType.NEW_DEVICE_SIGN_IN,
        template_context={"browser": browser, "address": address, "at": at.strftime("%Y-%m-%d %H:%M UTC"), "bank_name": bank_name},
        resource_type="user", resource_id=user.id,
    )


def notify_malware_detected(session: Session, tenant_id: uuid.UUID, *, filename: str, found: str, scanner: str, later: bool) -> None:
    """To everyone at the bank who manages it (tenant:manage)."""
    for admin in _recipients_for_permission(session, tenant_id, "tenant:manage", customer_id=None):
        _queue(session, tenant_id=tenant_id, recipient=admin, notification_type=NotificationType.MALWARE_DETECTED,
               template_context={"filename": filename, "found": found, "scanner": "ClamAV" if scanner == "clamav" else "VirusTotal",
                                 "later": later}, resource_type="file")


def notify_email_address(session: Session, user: User, *, destination: str, notification_type: NotificationType,
                         context: dict[str, Any], tenant_id: uuid.UUID | None = None) -> None:
    """An email about a person's address, sent to an address rather than to the person's
    current one (services/email_change_service.py): the new address's confirmation link, and
    the notice of a change to both the old and new. Always email."""
    session.add(Notification(
        tenant_id=tenant_id, recipient_user_id=user.id, notification_type=notification_type, channel=NotificationChannel.EMAIL,
        destination=destination, subject=_EMAIL_SUBJECTS[notification_type],
        body=_render_body(notification_type, NotificationChannel.EMAIL, context), resource_type="user", resource_id=user.id,
    ))
    session.flush()


def notify_invitation(
    session: Session, *, tenant_id: uuid.UUID, email: str, link: str, organization: str, bank_name: str,
    invited_by: str | None, expires_days: int, invitation_id: uuid.UUID,
) -> None:
    """A customer administrator's invitation (services/customer_people_service.py). Sent to an
    address, not a person: nobody has signed in with it here yet, or they're accepting for
    an identity they'll prove is theirs. Always email: it carries the one-time link."""
    session.add(Notification(
        tenant_id=tenant_id, recipient_user_id=None, notification_type=NotificationType.INVITATION,
        channel=NotificationChannel.EMAIL, destination=email,
        subject=f"{organization} invites you to OpenClear at {bank_name}",
        body=_render_body(NotificationType.INVITATION, NotificationChannel.EMAIL, {
            "link": link, "organization": organization, "bank_name": bank_name, "invited_by": invited_by,
            "expires_days": expires_days,
        }),
        resource_type="user_invitation", resource_id=invitation_id,
    ))
    session.flush()


def get_preferences(session: Session, user_id: uuid.UUID) -> dict[NotificationType, NotificationPreference]:
    rows = session.execute(select(NotificationPreference).where(NotificationPreference.user_id == user_id)).scalars().all()
    return {row.notification_type: row for row in rows}


def set_preference(
    session: Session, user_id: uuid.UUID, notification_type: NotificationType, *, email_enabled: bool, sms_enabled: bool
) -> NotificationPreference:
    pref = session.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id, NotificationPreference.notification_type == notification_type
        )
    ).scalar_one_or_none()
    if pref is None:
        pref = NotificationPreference(user_id=user_id, notification_type=notification_type)
        session.add(pref)
    pref.email_enabled = email_enabled
    pref.sms_enabled = sms_enabled
    session.flush()
    return pref


def set_phone(session: Session, user: User, phone: str | None) -> None:
    user.phone = phone or None
    session.flush()
