# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.customer_ml_setting import MlScoringMode
from openclear.services import customer_ml_service, customer_service


def _make_customer(db_session, tenant, customer_number="C-1"):
    customer = customer_service.create_customer(
        db_session, tenant.id, customer_service.CustomerInput(customer_number=customer_number, name="Acme Co")
    )
    db_session.commit()
    return customer


def test_get_mode_defaults_to_auto_when_no_setting_exists(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-ml-default")
    customer = _make_customer(db_session, tenant)

    assert customer_ml_service.get_mode(db_session, tenant.id, customer.id, "check") == MlScoringMode.AUTO


def test_set_mode_then_get_mode_round_trips(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-ml-roundtrip")
    customer = _make_customer(db_session, tenant)

    customer_ml_service.set_mode(db_session, tenant.id, customer.id, "check", MlScoringMode.GLOBAL)
    db_session.commit()

    assert customer_ml_service.get_mode(db_session, tenant.id, customer.id, "check") == MlScoringMode.GLOBAL
    # a different network for the same customer is unaffected
    assert customer_ml_service.get_mode(db_session, tenant.id, customer.id, "ach") == MlScoringMode.AUTO


def test_set_mode_twice_updates_the_same_row_not_a_duplicate(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="cust-ml-update")
    customer = _make_customer(db_session, tenant)

    customer_ml_service.set_mode(db_session, tenant.id, customer.id, "check", MlScoringMode.GLOBAL)
    db_session.commit()
    customer_ml_service.set_mode(db_session, tenant.id, customer.id, "check", MlScoringMode.CUSTOMER)
    db_session.commit()

    from openclear.repositories.customer_ml_setting_repo import CustomerMlSettingRepository

    rows = CustomerMlSettingRepository(db_session, tenant.id).list(customer_id=customer.id, network_code="check")
    assert len(rows) == 1
    assert rows[0].mode == MlScoringMode.CUSTOMER
