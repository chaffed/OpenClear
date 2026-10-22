# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Counting wrong passwords toward a lockout (FULL_REVIEW_PLAN.md FR-D7, S1).

One place for every form that checks a password: signing in (bank and platform), changing
one's own password, and accepting an invitation as someone who already has a sign-in. The count
is incremented in the database (`UPDATE ... SET n = n + 1 RETURNING n`), not read, added to and
written back, so guesses sent in parallel each count; and the lock follows the bank's own
policy (auth/login_service.py::lockout_policy) wherever the bank is known."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from openclear.domain.tenant import Tenant
from openclear.domain.user import User


@dataclass(frozen=True)
class Failure:
    attempts: int
    locked_now: bool  # this attempt locked the account
    minutes: int | None  # how long the lock lasts; None until an administrator unlocks it


def record_failure(session: Session, user: User, tenant: Tenant | None, *, settings=None) -> Failure:
    """One more wrong password for `user`, under `tenant`'s policy (the installation's when there's
    no bank, as for platform staff). Locks the account when the count reaches the threshold and
    emails its owner. Flushes; the caller commits, even though the request then fails."""
    from openclear.auth.login_service import LOCKED_UNTIL_UNLOCKED, lockout_policy
    from openclear.services import notification_service

    attempts = session.execute(
        update(User).where(User.id == user.id).values(failed_login_attempts=User.failed_login_attempts + 1)
        .returning(User.failed_login_attempts).execution_options(synchronize_session=False)
    ).scalar_one()
    set_committed_value(user, "failed_login_attempts", attempts)
    threshold, minutes, until_unlocked = lockout_policy(tenant, settings)
    if attempts < threshold:
        return Failure(attempts, False, None)
    # At or past the threshold (past it again once an earlier lock ran out): lock, unless it's
    # already locked. Done as one conditional UPDATE, so of several guesses arriving together only
    # one locks it and emails the owner.
    now = datetime.now(timezone.utc)
    locked_until = LOCKED_UNTIL_UNLOCKED if until_unlocked else now + timedelta(minutes=minutes)
    newly = session.execute(
        update(User).where(User.id == user.id, or_(User.locked_until.is_(None), User.locked_until <= now))
        .values(locked_until=locked_until).execution_options(synchronize_session=False)
    ).rowcount == 1
    if not newly:
        return Failure(attempts, False, None)
    set_committed_value(user, "locked_until", locked_until)
    notification_service.notify_account_locked(session, user, minutes=None if until_unlocked else minutes)
    return Failure(attempts, True, None if until_unlocked else minutes)


def record_success(session: Session, user: User) -> None:
    """The right password: the count starts again."""
    if user.failed_login_attempts or user.locked_until is not None:
        user.failed_login_attempts, user.locked_until = 0, None
        session.flush()
