# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Dual control as a table (DUAL_CONTROL_PLAN.md, Testing): every registered kind is well formed;
each second-person switch is refused for each sort of person who can't approve (DC-D16); each tier
waits only when it should; and the rules for deciding hold."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from openclear.auth.permissions import PERMISSION_CATALOG
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.services import change_kinds, security_events, security_group_service, user_service
from openclear.services import change_request_service as changes
from openclear.services.change_kinds import ChangeError
from tests.conftest import TenantFactory
from tests.test_auth.test_dual_control import _only, _policy

_TIERS = {"protection", "sensitive", "access", "customer"}
_KINDS = sorted(change_kinds.all_kinds(), key=lambda k: k.key)


@pytest.mark.parametrize("kind", _KINDS, ids=[k.key for k in _KINDS])
def test_every_kind_is_well_formed(kind):
    assert kind.title and kind.tier in _TIERS and kind.scope in {"bank", "customer", "provider"}
    assert kind.permission in PERMISSION_CATALOG or kind.permission == "platform:operator"
    assert (kind.scope == "provider") == (kind.permission == "platform:operator")  # only operators approve the provider's
    if kind.scope == "provider":
        assert kind.tier == "protection"  # always two operators (DC-D12)
    if isinstance(kind.action, str) and kind.scope != "provider":
        assert kind.action in security_events.AUDIT_EVENTS or kind.action in security_events.CATALOG, kind.action


def _person(db_session, tenant, name, group="Admin", **user_fields):
    group_row = security_group_service.get_security_group_by_name(db_session, tenant.id, group)
    user = user_service.create_user_with_membership(db_session, tenant.id, email=f"{name}@{tenant.slug}.example.com",
                                                    password=TenantFactory.PASSWORD, security_group_id=group_row.id)
    for field_name, value in user_fields.items():
        setattr(user, field_name, value)
    db_session.commit()
    return user


def _membership(db_session, user) -> TenantMembership:
    return db_session.execute(select(TenantMembership).where(TenantMembership.user_id == user.id)).scalar_one()


def _not_an_approver(kind_of_person: str, db_session, tenant, other_tenant):
    if kind_of_person == "a viewer":
        _person(db_session, tenant, "looker", group="Viewer")
    elif kind_of_person == "an API client":
        _person(db_session, tenant, "robot", is_service_account=True)
    elif kind_of_person == "a deactivated admin":
        _membership(db_session, _person(db_session, tenant, "gone")).is_active = False
    elif kind_of_person == "an admin waiting for approval":
        _membership(db_session, _person(db_session, tenant, "waiting")).pending_approval_at = datetime.now(timezone.utc)
    elif kind_of_person == "another bank's admin":
        _person(db_session, other_tenant, "elsewhere")
    elif kind_of_person == "platform staff":
        _person(db_session, tenant, "staff", platform_role="support")
    db_session.commit()


_SWITCHES = ["sensitive_changes_need_approval", "access_changes_need_approval", "customer_changes_need_approval"]
_NOBODY = ["nobody", "a viewer", "an API client", "a deactivated admin", "an admin waiting for approval", "another bank's admin",
           "platform staff"]


@pytest.mark.parametrize("who", _NOBODY)
@pytest.mark.parametrize("switch", _SWITCHES)
def test_a_switch_is_refused_without_a_real_second_approver(db_session, tenant_factory, switch, who):
    tenant, _a, users = tenant_factory.make(slug=f"dcm-{_SWITCHES.index(switch)}-{_NOBODY.index(who)}")
    other, _b, _other_users = tenant_factory.make(slug=f"dcm-o-{_SWITCHES.index(switch)}-{_NOBODY.index(who)}")
    _only(db_session, tenant, "admin", users)
    _not_an_approver(who, db_session, tenant, other)
    with pytest.raises(ChangeError, match="Nobody else can approve"):
        changes.request_or_apply(db_session, "approval_switches", {"values": {switch: True}}, tenant_id=tenant.id, user_id=users["admin"].id)
    db_session.expire_all()
    assert getattr(db_session.get(Tenant, tenant.id), switch) is False

    _person(db_session, tenant, "second")  # one real approver is enough
    assert changes.request_or_apply(db_session, "approval_switches", {"values": {switch: True}}, tenant_id=tenant.id,
                                    user_id=users["admin"].id).applied


@pytest.mark.parametrize("tier,switch", [("protection", None), ("sensitive", "sensitive_changes_need_approval"),
                                         ("access", "access_changes_need_approval")])
def test_each_tier_waits_only_when_it_should(db_session, tenant_factory, tier, switch):
    tenant, _a, _users = tenant_factory.make(slug=f"dcm-tier-{tier}")
    kinds = [k for k in _KINDS if k.tier == tier and k.scope == "bank"]
    assert kinds
    for kind in kinds:
        assert changes.protected(db_session, kind, tenant.id, None) is (switch is None), kind.key
    if switch:
        setattr(db_session.get(Tenant, tenant.id), switch, True)
        db_session.commit()
        assert all(changes.protected(db_session, kind, tenant.id, None) for kind in kinds)


def test_customer_kinds_wait_when_the_bank_or_the_customer_asks(db_session, tenant_factory):
    from openclear.domain.customer import Customer

    tenant, _a, _users = tenant_factory.make(slug="dcm-tier-customer")
    customer = Customer(tenant_id=tenant.id, customer_number="C-1", name="Widgets")
    db_session.add(customer)
    db_session.commit()
    kinds = [k for k in _KINDS if k.scope == "customer" and k.tier != "protection"]
    assert kinds and not any(changes.protected(db_session, k, tenant.id, customer.id) for k in kinds)
    customer.changes_need_approval = True
    db_session.commit()
    assert all(changes.protected(db_session, k, tenant.id, customer.id) for k in kinds)
    customer.changes_need_approval = False
    db_session.get(Tenant, tenant.id).customer_changes_need_approval = True
    db_session.commit()
    assert all(changes.protected(db_session, k, tenant.id, customer.id) for k in kinds)


def test_who_may_decide(db_session, tenant_factory):
    tenant, _a, users = tenant_factory.make(slug="dcm-decide")
    second = _person(db_session, tenant, "second")
    changes.request_or_apply(db_session, "password_policy", _policy(), tenant_id=tenant.id, user_id=users["admin"].id)
    waiting = changes.request_or_apply(db_session, "password_policy", _policy(password_min_length=8), tenant_id=tenant.id,
                                       user_id=users["admin"].id).change
    db_session.commit()
    with pytest.raises(ChangeError, match="already waiting"):  # one open request per setting
        changes.request_or_apply(db_session, "password_policy", _policy(password_min_length=9), tenant_id=tenant.id,
                                 user_id=users["admin"].id)
    assert not changes.can_decide(db_session, waiting, users["admin"].id)  # not their own
    assert not changes.can_decide(db_session, waiting, users["viewer"].id)  # not without the permission
    assert changes.can_decide(db_session, waiting, second.id)
    with pytest.raises(ChangeError, match="Say why"):
        changes.decide(db_session, tenant.id, waiting.id, approve=False, user_id=second.id)
    changes.decide(db_session, tenant.id, waiting.id, approve=False, user_id=second.id, reason="Too weak")
    db_session.commit()
    assert db_session.get(Tenant, tenant.id).password_min_length == 12
