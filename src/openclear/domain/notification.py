# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from openclear.db.base import Base, new_uuid


class NotificationType(str, enum.Enum):
    EXCEPTION_CREATED = "exception_created"
    RECOMMENDATION_AWAITING_APPROVAL = "recommendation_awaiting_approval"
    ACCOUNT_LOCKED = "account_locked"
    ACCOUNT_UNLOCKED = "account_unlocked"
    # services/auto_disposition_service.py finalized an exception with no human decider —
    # see domain/decision.py::DecisionSource.
    EXCEPTION_AUTO_DECIDED = "exception_auto_decided"
    # services/user_service.py — the user changed their own password, or an admin reset it.
    # Always emailed (see notification_service._ALWAYS_EMAIL_TYPES): an unexpected one is
    # the first sign an account was taken over.
    PASSWORD_CHANGED = "password_changed"
    # services/reminder_service.py: items due soon, one message per person per reminder
    # (UX_PLAN.md U3).
    DEADLINE_REMINDER = "deadline_reminder"
    # services/customer_people_service.py: a customer's administrator invited this person.
    INVITATION = "invitation"
    # services/password_reset_service.py: an administrator reset this person's password; the email
    # carries the one-time link to choose a new one (ASVS 5.0 6.4.6). Always email.
    PASSWORD_RESET_LINK = "password_reset_link"
    # web/routers/security_settings.py, api/v1/webauthn.py: a security key was added to or removed
    # from this person's sign-in. Always email: an unexpected one means someone else is in.
    SECURITY_KEY_CHANGED = "security_key_changed"
    # services/customer_approval_service.py: the bank approved or rejected someone the
    # customer's side added (MOBILE_PLAN.md R6); sent to the customer's administrators.
    CUSTOMER_USER_DECIDED = "customer_user_decided"
    # services/attestation_service.py: the bank asks the customer's people who can attest to
    # attest its own CUECs for the year that ended (CONTROLS_PLAN.md CT-D13).
    ATTESTATION_REQUESTED = "attestation_requested"
    # services/inactivity_service.py: the person's access to a bank will be turned off or removed
    # in a week unless they sign in.
    INACTIVITY_WARNING = "inactivity_warning"
    # services/change_request_service.py (DUAL_CONTROL_PLAN.md): a change waits for this person's
    # approval (or, with nobody else to approve, applies after 24 hours); and the requester's
    # change was decided.
    CHANGE_REQUESTED = "change_requested"
    CHANGE_DECIDED = "change_decided"
    # services/controls_service.py (DUAL_CONTROL_PLAN.md DC-D17): a second-person rule the bank has
    # on no longer has anyone else to approve, so its changes now take the 24-hour cooling-off.
    APPROVER_GONE = "approver_gone"


class NotificationChannel(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"


class NotificationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


_MAX_SEND_ATTEMPTS = 3


class Notification(Base):
    """One outbound email or SMS, queued by services/notification_service.py and drained
    by workers/tasks.py::notification_dispatch_job — never sent inline in a request, so a
    slow/unreachable provider never blocks the action that triggered it. `destination` is
    a snapshot of the recipient's email/phone at queue time, not a live join to User, so a
    later contact-info change never retroactively alters an already-queued message's
    target (same "snapshot at time of action" reasoning as this app's other
    audit-adjacent tables)."""

    __tablename__ = "notification"
    __table_args__ = (Index("ix_notification_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    # Nullable: ACCOUNT_LOCKED/ACCOUNT_UNLOCKED are about the User identity itself, not
    # any one tenant, since a brute force targets the one shared password regardless of
    # which tenant the login attempt was against (see User.failed_login_attempts).
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"), nullable=True, index=True)
    # Nullable too: an invitation goes to an email nobody has signed in with yet
    # (services/customer_people_service.py); `destination` is the address either way.
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id"), nullable=True, index=True)
    notification_type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type", native_enum=False, length=40), nullable=False
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(NotificationChannel, name="notification_channel", native_enum=False, length=10), nullable=False
    )
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)  # SMS has none
    body: Mapped[str] = mapped_column(Text, nullable=False)  # already rendered, not re-templated at send time

    # Mirrors audit_log_entry's own resource linkage — optional, for a future "view the
    # related record" link; nothing currently reads these back.
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, name="notification_status", native_enum=False, length=10),
        nullable=False,
        default=NotificationStatus.PENDING,
    )
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def exhausted_retries(self) -> bool:
        return self.attempt_count >= _MAX_SEND_ATTEMPTS


class NotificationPreference(Base):
    """One row per (user, notification_type) a user has explicitly configured — absence
    of a row is not "no preference," it's the type's own hardcoded default (see
    services/notification_service.py::_effective_preference), so most users never need a
    row at all. Global to the User identity, not tenant-owned, matching User itself and
    WebauthnCredential's own shape (one login, one set of preferences, reused across
    every tenant that login has a membership in)."""

    __tablename__ = "notification_preference"
    __table_args__ = (UniqueConstraint("user_id", "notification_type", name="uq_notification_pref_user_type"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    notification_type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type", native_enum=False, length=40), nullable=False
    )
    email_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    sms_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
