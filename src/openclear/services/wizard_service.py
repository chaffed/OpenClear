# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import dataclasses
import uuid
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from openclear.domain.account import Account
from openclear.domain.customer import Customer
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.wizard_step_ack import WizardStepAck

AutoCheck = Callable[[Session, uuid.UUID, "uuid.UUID | None"], bool]


@dataclass(frozen=True, slots=True)
class WizardStep:
    """One guided-checklist step. `auto_check` is None for a "manual acknowledgment"
    step (no single queryable completion fact — the user checks it off themselves,
    persisted via WizardStepAck); set for an "auto-detected" step whose completion is a
    real fact this always re-checks against live data, never a stale checkbox."""

    key: str
    title: str
    description: str
    link_text: str
    link_url: str
    optional: bool = False
    auto_check: AutoCheck | None = None


@dataclass(frozen=True, slots=True)
class WizardStepView:
    step: WizardStep
    is_complete: bool


def _bank_has_accounts(session: Session, tenant_id: uuid.UUID, _customer_id: uuid.UUID | None) -> bool:
    stmt = select(Account.id).where(Account.tenant_id == tenant_id).limit(1)
    return session.execute(stmt).first() is not None


def _bank_has_staff_beyond_bootstrap(session: Session, tenant_id: uuid.UUID, _customer_id: uuid.UUID | None) -> bool:
    # The bootstrap admin created at provisioning already counts as one active
    # membership — this step is "complete" once someone else has been added too.
    count = session.execute(
        select(func.count(TenantMembership.id)).where(
            TenantMembership.tenant_id == tenant_id, TenantMembership.is_active.is_(True)
        )
    ).scalar_one()
    return count > 1


def _bank_has_customer(session: Session, tenant_id: uuid.UUID, _customer_id: uuid.UUID | None) -> bool:
    stmt = select(Customer.id).where(Customer.tenant_id == tenant_id).limit(1)
    return session.execute(stmt).first() is not None


def _customer_has_accounts(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> bool:
    stmt = select(Account.id).where(Account.tenant_id == tenant_id, Account.customer_id == customer_id).limit(1)
    return session.execute(stmt).first() is not None


def _customer_has_users(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None) -> bool:
    stmt = (
        select(TenantMembership.id)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.customer_id == customer_id,
            TenantMembership.is_active.is_(True),
        )
        .limit(1)
    )
    return session.execute(stmt).first() is not None


BANK_STEPS: list[WizardStep] = [
    WizardStep(
        key="branding",
        title="Set your organization's branding",
        description="Display name, logo, and accent color — shown throughout the app and on your own login page, before anyone signs in.",
        link_text="Go to Settings",
        link_url="/ui/settings",
    ),
    WizardStep(
        key="dual_control",
        title="Decide whether to require dual control",
        description="Maker/checker: when enabled, a different person must make the final pay/return decision than the one who recommended it.",
        link_text="Go to Settings",
        link_url="/ui/settings",
    ),
    WizardStep(
        key="fraud_model",
        title="Decide whether to use fraud scoring",
        description="Optional and off to start: a model trained only on your own decisions shows an advisory fraud score beside each item. It never decides one. Mark this done either way.",
        link_text="Go to Fraud scoring",
        link_url="/ui/settings/fraud-model",
    ),
    WizardStep(
        key="security_groups",
        title="Review your security groups",
        description="Four default groups (Admin, Preparer, Approver, Viewer) are seeded automatically — keep them, edit their permissions, or create your own.",
        link_text="Go to Security Groups",
        link_url="/ui/security-groups",
    ),
    WizardStep(
        key="accounts",
        title="Add your accounts",
        description="The bank accounts OpenClear will monitor for issued checks, presented checks, and ACH activity.",
        link_text="Add an account",
        link_url="/ui/accounts/new",
        auto_check=_bank_has_accounts,
    ),
    WizardStep(
        key="staff",
        title="Add your staff",
        description="Invite your team and assign each person a security group.",
        link_text="Add a user",
        link_url="/ui/users/new",
        auto_check=_bank_has_staff_beyond_bootstrap,
    ),
    WizardStep(
        key="sso",
        title="Set up single sign-on (optional)",
        description="Let your staff sign in with Okta or Azure AD instead of, or alongside, a password.",
        link_text="Go to Single Sign-On",
        link_url="/ui/admin/sso",
        optional=True,
    ),
    WizardStep(
        key="first_customer",
        title="Add your first customer (optional)",
        description="If you serve business clients directly with their own segregated data, add your first one here.",
        link_text="Add a customer",
        link_url="/ui/customers/new",
        optional=True,
        auto_check=_bank_has_customer,
    ),
]

CUSTOMER_STEPS: list[WizardStep] = [
    WizardStep(
        key="accounts",
        title="Add this customer's accounts",
        description="The accounts belonging to this customer that OpenClear will monitor.",
        link_text="Add an account",
        link_url="/ui/accounts/new?customer_id={customer_id}&return=/ui/customers/{customer_id}/wizard",
        auto_check=_customer_has_accounts,
    ),
    WizardStep(
        key="users",
        title="Add this customer's users",
        description="Staff — the bank's own or this customer's employees — scoped to just this customer's data.",
        link_text="Invite someone",
        link_url="/ui/customers/{customer_id}?return=/ui/customers/{customer_id}/wizard#invite",
        auto_check=_customer_has_users,
    ),
    WizardStep(
        key="sso",
        title="Set up this customer's own single sign-on (optional)",
        description="Independent of the bank's own SSO setup — lets this customer's staff sign in with their own Okta or Azure AD.",
        link_text="Go to Single Sign-On",
        link_url="/ui/customers/{customer_id}/sso",
        optional=True,
    ),
    WizardStep(
        key="ml_scoring",
        title="Review ML scoring for this customer (optional)",
        description="Exceptions are scored with the bank-wide model by default; a customer-specific model can take over automatically once this customer has enough of their own decision history.",
        link_text="Go to customer detail",
        link_url="/ui/customers/{customer_id}",
        optional=True,
    ),
]


def _is_acknowledged(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, step_key: str) -> bool:
    stmt = (
        select(WizardStepAck.id)
        .where(WizardStepAck.tenant_id == tenant_id, WizardStepAck.customer_id == customer_id, WizardStepAck.step_key == step_key)
        .limit(1)
    )
    return session.execute(stmt).first() is not None


def _resolve(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, step: WizardStep) -> WizardStepView:
    is_complete = (
        step.auto_check(session, tenant_id, customer_id)
        if step.auto_check is not None
        else _is_acknowledged(session, tenant_id, customer_id, step.key)
    )
    return WizardStepView(step=step, is_complete=is_complete)


def get_bank_wizard_steps(session: Session, tenant_id: uuid.UUID) -> list[WizardStepView]:
    return [_resolve(session, tenant_id, None, step) for step in BANK_STEPS]


def get_customer_wizard_steps(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> list[WizardStepView]:
    views = []
    for step in CUSTOMER_STEPS:
        # Each step's link names the customer, and the account and user forms come back to
        # this checklist ({customer_id} in link_url; UI_PLAN.md UI-D12).
        scoped_step = dataclasses.replace(step, link_url=step.link_url.format(customer_id=customer_id))
        views.append(_resolve(session, tenant_id, customer_id, scoped_step))
    return views


def bank_progress(session: Session, tenant_id: uuid.UUID) -> tuple[int, int]:
    """(required steps done, required steps), for the control panel's tile."""
    required = [view for view in get_bank_wizard_steps(session, tenant_id) if not view.step.optional]
    return sum(view.is_complete for view in required), len(required)


def is_bank_wizard_complete(session: Session, tenant_id: uuid.UUID) -> bool:
    return all(view.is_complete for view in get_bank_wizard_steps(session, tenant_id) if not view.step.optional)


def is_customer_wizard_complete(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> bool:
    return all(
        view.is_complete for view in get_customer_wizard_steps(session, tenant_id, customer_id) if not view.step.optional
    )


def acknowledge_step(
    session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, step_key: str, user_id: uuid.UUID
) -> None:
    if _is_acknowledged(session, tenant_id, customer_id, step_key):
        return
    session.add(WizardStepAck(tenant_id=tenant_id, customer_id=customer_id, step_key=step_key, acknowledged_by_user_id=user_id))
    session.flush()


def unacknowledge_step(session: Session, tenant_id: uuid.UUID, customer_id: uuid.UUID | None, step_key: str) -> None:
    stmt = select(WizardStepAck).where(
        WizardStepAck.tenant_id == tenant_id, WizardStepAck.customer_id == customer_id, WizardStepAck.step_key == step_key
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is not None:
        session.delete(row)
        session.flush()
