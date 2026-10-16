# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Access reviews (CONTROLS_PLAN.md CT3; domain/access_review.py). The bank reviews every
membership (staff, customers' people, API clients); a self-managing customer reviews its own
people. Starting one takes a snapshot of each membership's role, scope, accounts and last
sign-in; each is then confirmed, marked changed (with a note) or removed (deactivated, through
the same paths as the Users and People pages, so their guardrails apply). When the last one is
decided the review completes, with a SHA-256 over every decision, recorded by the caller in the
signed action log."""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from openclear.domain.access_review import AccessReview, AccessReviewItem
from openclear.domain.customer import Customer
from openclear.domain.security_group import SecurityGroup
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User

DECISIONS = ("confirmed", "changed", "removed")


class ReviewError(ValueError):
    """The message is fit to show."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def open_review(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> AccessReview | None:
    return session.execute(select(AccessReview).where(
        AccessReview.tenant_id == tenant_id, AccessReview.completed_at.is_(None),
        AccessReview.customer_id == customer_id if customer_id else AccessReview.customer_id.is_(None))).scalar_one_or_none()


def reviews(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, *, limit: int = 50) -> list[AccessReview]:
    return list(session.execute(select(AccessReview).where(
        AccessReview.tenant_id == tenant_id,
        AccessReview.customer_id == customer_id if customer_id else AccessReview.customer_id.is_(None))
        .order_by(AccessReview.started_at.desc()).limit(limit)).scalars())


def get_review(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, review_id: uuid.UUID) -> AccessReview | None:
    """A review in this scope: the bank's own, or this customer's."""
    found = session.get(AccessReview, review_id)
    if found is None or found.tenant_id != tenant_id or found.customer_id != customer_id:
        return None
    return found


def items(session: Session, review: AccessReview) -> list[AccessReviewItem]:
    return list(session.execute(select(AccessReviewItem).where(AccessReviewItem.review_id == review.id)
                                .order_by(AccessReviewItem.decision.is_not(None), AccessReviewItem.scope, AccessReviewItem.who)).scalars())


def start(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, *, user_id: uuid.UUID | None) -> AccessReview:
    """A new review of every active membership in scope (one open at a time per scope)."""
    from openclear.domain.api_client import ApiClient
    from openclear.services import account_access_service

    if open_review(session, tenant_id, customer_id) is not None:
        raise ReviewError("A review is already open: finish it first.")
    stmt = (select(TenantMembership, User, SecurityGroup.name, Customer.name)
            .join(User, User.id == TenantMembership.user_id)
            .outerjoin(SecurityGroup, SecurityGroup.id == TenantMembership.security_group_id)
            .outerjoin(Customer, Customer.id == TenantMembership.customer_id)
            .where(TenantMembership.tenant_id == tenant_id, TenantMembership.is_active.is_(True), User.is_active.is_(True)))
    if customer_id is not None:
        stmt = stmt.where(TenantMembership.customer_id == customer_id, User.is_service_account.is_(False))
    clients = {c.membership_id: c.name for c in session.execute(select(ApiClient).where(ApiClient.tenant_id == tenant_id,
                                                                                        ApiClient.revoked_at.is_(None))).scalars()}
    review = AccessReview(tenant_id=tenant_id, customer_id=customer_id, started_by_user_id=user_id, started_at=_now())
    session.add(review)
    session.flush()
    count = 0
    for membership, user, role, customer_name in session.execute(stmt).all():
        if user.is_service_account and membership.id not in clients:
            continue
        accounts = "All accounts"
        if membership.customer_id is not None and not membership.all_accounts:
            accounts = account_access_service.describe(account_access_service.get_access(session, tenant_id, membership.id))
        session.add(AccessReviewItem(
            tenant_id=tenant_id, review_id=review.id, membership_id=membership.id,
            who=f"API client: {clients[membership.id]}" if user.is_service_account else user.email,
            role=role or "—", scope=customer_name or "Bank-wide", accounts=accounts[:500], last_sign_in=user.last_login_at,
        ))
        count += 1
    review.item_count = count
    session.flush()
    return review


def _item(session: Session, review: AccessReview, item_id: uuid.UUID) -> AccessReviewItem:
    item = session.get(AccessReviewItem, item_id)
    if item is None or item.review_id != review.id:
        raise ReviewError("That isn't in this review.")
    return item


def decide(session: Session, review: AccessReview, item_id: uuid.UUID, decision: str, *, note: str = "",
           user_id: uuid.UUID) -> AccessReviewItem:
    """Confirm, mark changed (a note says what) or remove (the membership is deactivated)."""
    from openclear.services import customer_people_service

    if review.completed_at is not None:
        raise ReviewError("This review is complete.")
    if decision not in DECISIONS:
        raise ReviewError("Confirm, mark changed or remove.")
    note = (note or "").strip()
    if decision == "changed" and not note:
        raise ReviewError("Say what changed.")
    item = _item(session, review, item_id)
    if decision == "removed":
        membership = session.get(TenantMembership, item.membership_id)
        if membership is not None and membership.is_active:
            if review.customer_id is not None:
                try:
                    customer_people_service.set_active(session, review.tenant_id, review.customer_id, membership.id, False)
                except customer_people_service.PeopleError as exc:
                    raise ReviewError(str(exc)) from None
            else:
                _remove_for_the_bank(session, review.tenant_id, membership)
    item.decision, item.note, item.decided_by_user_id, item.decided_at = decision, note or None, user_id, _now()
    session.flush()
    return item


def _remove_for_the_bank(session: Session, tenant_id: uuid.UUID, membership: TenantMembership) -> None:
    from openclear.domain.api_client import ApiClient
    from openclear.services import api_client_service, user_service

    client = session.execute(select(ApiClient).where(ApiClient.membership_id == membership.id)).scalar_one_or_none()
    if client is not None:
        api_client_service.revoke(session, tenant_id, client.id)
    else:
        user_service.deactivate_membership(session, tenant_id, membership.id)


def digest(decided: list[AccessReviewItem]) -> str:
    rows = sorted([str(i.membership_id), i.who, i.role, i.scope, i.decision, i.note or ""] for i in decided)
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def complete_if_done(session: Session, review: AccessReview, *, user_id: uuid.UUID) -> bool:
    """Completes the review once every item is decided. Returns whether it just completed."""
    all_items = items(session, review)
    if review.completed_at is not None or any(i.decision is None for i in all_items):
        return False
    review.completed_at, review.completed_by_user_id, review.decisions_sha256 = _now(), user_id, digest(all_items)
    session.flush()
    return True


def tally(all_items: list[AccessReviewItem]) -> dict[str, int]:
    out = {d: 0 for d in DECISIONS}
    out["waiting"] = 0
    for i in all_items:
        out[i.decision or "waiting"] += 1
    return out
