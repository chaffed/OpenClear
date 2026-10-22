# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.services import account_service, customer_service, provisioning_service, security_group_service, user_service, wizard_service
from tests.conftest import TenantFactory


def _fresh_tenant(db_session, slug):
    result = provisioning_service.create_tenant_with_admin(
        db_session, tenant_name="Fresh Bank", tenant_slug=slug, admin_email=f"admin@{slug}.example.com", admin_password=TenantFactory.PASSWORD
    )
    db_session.commit()
    return result.tenant, result.admin_user


def _step_map(views):
    return {v.step.key: v.is_complete for v in views}


def test_bank_wizard_fresh_tenant_has_required_steps_incomplete(db_session):
    tenant, _admin = _fresh_tenant(db_session, "wizard-fresh-bank")

    steps = _step_map(wizard_service.get_bank_wizard_steps(db_session, tenant.id))

    assert steps["branding"] is False
    assert steps["dual_control"] is False
    assert steps["security_groups"] is False
    assert steps["accounts"] is False
    assert steps["staff"] is False
    assert steps["sso"] is False
    assert steps["first_customer"] is False
    assert wizard_service.is_bank_wizard_complete(db_session, tenant.id) is False


def test_bank_wizard_accounts_step_auto_completes(db_session):
    tenant, _admin = _fresh_tenant(db_session, "wizard-accounts")

    account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number="1", name="Operating"))
    db_session.commit()

    steps = _step_map(wizard_service.get_bank_wizard_steps(db_session, tenant.id))
    assert steps["accounts"] is True


def test_bank_wizard_staff_step_auto_completes_once_second_user_added(db_session):
    tenant, _admin = _fresh_tenant(db_session, "wizard-staff")
    steps = _step_map(wizard_service.get_bank_wizard_steps(db_session, tenant.id))
    assert steps["staff"] is False

    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    user_service.create_user_with_membership(db_session, tenant.id, email="second@wizard-staff.example.com", password=TenantFactory.PASSWORD, security_group_id=group.id)
    db_session.commit()

    steps = _step_map(wizard_service.get_bank_wizard_steps(db_session, tenant.id))
    assert steps["staff"] is True


def test_bank_wizard_manual_step_acknowledge_and_unacknowledge_round_trip(db_session):
    tenant, admin = _fresh_tenant(db_session, "wizard-manual")

    steps = _step_map(wizard_service.get_bank_wizard_steps(db_session, tenant.id))
    assert steps["branding"] is False

    wizard_service.acknowledge_step(db_session, tenant.id, None, "branding", admin.id)
    db_session.commit()
    steps = _step_map(wizard_service.get_bank_wizard_steps(db_session, tenant.id))
    assert steps["branding"] is True

    wizard_service.unacknowledge_step(db_session, tenant.id, None, "branding")
    db_session.commit()
    steps = _step_map(wizard_service.get_bank_wizard_steps(db_session, tenant.id))
    assert steps["branding"] is False


def test_acknowledge_step_is_idempotent(db_session):
    from openclear.domain.wizard_step_ack import WizardStepAck

    tenant, admin = _fresh_tenant(db_session, "wizard-idempotent")
    wizard_service.acknowledge_step(db_session, tenant.id, None, "branding", admin.id)
    wizard_service.acknowledge_step(db_session, tenant.id, None, "branding", admin.id)
    db_session.commit()

    rows = db_session.query(WizardStepAck).filter_by(tenant_id=tenant.id, step_key="branding").all()
    assert len(rows) == 1


def test_bank_wizard_complete_ignores_optional_steps(db_session):
    tenant, admin = _fresh_tenant(db_session, "wizard-optional")
    account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number="1", name="Operating"))
    group = security_group_service.get_security_group_by_name(db_session, tenant.id, "Preparer")
    user_service.create_user_with_membership(db_session, tenant.id, email="second@wizard-optional.example.com", password=TenantFactory.PASSWORD, security_group_id=group.id)
    for key in ("branding", "dual_control", "fraud_model", "security_groups"):
        wizard_service.acknowledge_step(db_session, tenant.id, None, key, admin.id)
    db_session.commit()

    # sso and first_customer (both optional) are still incomplete...
    steps = _step_map(wizard_service.get_bank_wizard_steps(db_session, tenant.id))
    assert steps["sso"] is False
    assert steps["first_customer"] is False
    # ...but the wizard is still considered complete, since neither is required.
    assert wizard_service.is_bank_wizard_complete(db_session, tenant.id) is True


def test_customer_wizard_steps_are_scoped_per_customer(db_session, tenant_factory):
    tenant, _house_account, _users = tenant_factory.make(slug="wizard-customer-scope")
    customer_a = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-A", name="A"))
    customer_b = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-B", name="B"))
    db_session.commit()

    steps_a = _step_map(wizard_service.get_customer_wizard_steps(db_session, tenant.id, customer_a.id))
    steps_b = _step_map(wizard_service.get_customer_wizard_steps(db_session, tenant.id, customer_b.id))
    assert steps_a["accounts"] is False
    assert steps_b["accounts"] is False

    account_service.create_account(db_session, tenant.id, account_service.AccountInput(account_number="A-1", name="A Acct", customer_id=customer_a.id))
    db_session.commit()

    steps_a = _step_map(wizard_service.get_customer_wizard_steps(db_session, tenant.id, customer_a.id))
    steps_b = _step_map(wizard_service.get_customer_wizard_steps(db_session, tenant.id, customer_b.id))
    assert steps_a["accounts"] is True
    assert steps_b["accounts"] is False

    # customer wizard link_urls are scoped to the right customer
    connections_step = next(v for v in wizard_service.get_customer_wizard_steps(db_session, tenant.id, customer_a.id) if v.step.key == "sso")
    assert f"/ui/customers/{customer_a.id}/sso" == connections_step.step.link_url


def test_wizard_step_ack_scope_does_not_leak_between_bank_and_customer(db_session, tenant_factory):
    tenant, _house_account, users = tenant_factory.make(slug="wizard-ack-scope")
    customer = customer_service.create_customer(db_session, tenant.id, customer_service.CustomerInput(customer_number="C-1", name="Acme"))
    db_session.commit()

    # Both wizards happen to share the step key "sso" — acknowledging the bank-wide one
    # must not mark the customer-scoped one (or vice versa) as done.
    wizard_service.acknowledge_step(db_session, tenant.id, None, "sso", users["admin"].id)
    db_session.commit()

    bank_steps = _step_map(wizard_service.get_bank_wizard_steps(db_session, tenant.id))
    customer_steps = _step_map(wizard_service.get_customer_wizard_steps(db_session, tenant.id, customer.id))
    assert bank_steps["sso"] is True
    assert customer_steps["sso"] is False
