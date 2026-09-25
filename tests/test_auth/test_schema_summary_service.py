# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.domain.customer import Customer
from openclear.domain.ml_model import MlModel
from openclear.domain.notification import NotificationPreference, NotificationType
from openclear.domain.platform_api_key import PlatformApiKey
from openclear.services import schema_summary_service


def _counts_dict(db_session, tenant_id):
    return dict(schema_summary_service.table_record_counts(db_session, tenant_id))


def test_counts_basic_tenant_scoped_tables(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="schema-summary-basic")

    counts = _counts_dict(db_session, tenant.id)

    assert counts["tenant"] == 1
    assert counts["account"] == 1
    assert counts["user"] == len(users)


def test_counts_are_isolated_between_tenants(db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="schema-summary-tenant-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="schema-summary-tenant-b")

    counts_a = _counts_dict(db_session, tenant_a.id)
    counts_b = _counts_dict(db_session, tenant_b.id)

    assert counts_a["tenant"] == 1
    assert counts_b["tenant"] == 1
    assert counts_a["user"] == len(users_a)
    # Each tenant's own account count reflects only its own account, not the other
    # tenant's, despite both existing in the same database.
    assert counts_a["account"] == 1
    assert counts_b["account"] == 1


def test_ml_model_excludes_global_model_and_other_tenants(db_session, tenant_factory):
    tenant_a, account_a, _users_a = tenant_factory.make(slug="schema-summary-ml-a")
    tenant_b, account_b, _users_b = tenant_factory.make(slug="schema-summary-ml-b")

    customer_a = db_session.get(Customer, account_a.customer_id) if account_a.customer_id else None
    customer_b = db_session.get(Customer, account_b.customer_id) if account_b.customer_id else None
    # TenantFactory's account isn't assigned to a customer by default -- create one each
    # so there's a real customer_id to scope MlModel rows against.
    if customer_a is None:
        customer_a = Customer(tenant_id=tenant_a.id, customer_number="CUST-A", name="Customer A")
        db_session.add(customer_a)
    if customer_b is None:
        customer_b = Customer(tenant_id=tenant_b.id, customer_number="CUST-B", name="Customer B")
        db_session.add(customer_b)
    db_session.flush()

    db_session.add_all(
        [
            # The genuinely global, tenant-agnostic model -- must never be counted for
            # any tenant, to avoid leaking any cross-tenant signal.
            MlModel(network_code="check", customer_id=None, version="v1", algorithm="rf", artifact_path="/tmp/global.joblib"),
            MlModel(
                network_code="check", customer_id=customer_a.id, version="v1", algorithm="rf", artifact_path="/tmp/a.joblib"
            ),
            MlModel(
                network_code="check", customer_id=customer_b.id, version="v1", algorithm="rf", artifact_path="/tmp/b.joblib"
            ),
        ]
    )
    db_session.commit()

    counts_a = _counts_dict(db_session, tenant_a.id)
    counts_b = _counts_dict(db_session, tenant_b.id)

    assert counts_a["ml_model"] == 1
    assert counts_b["ml_model"] == 1


def test_notification_preference_scoped_via_tenant_membership(db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="schema-summary-notif-a")
    tenant_b, _account_b, users_b = tenant_factory.make(slug="schema-summary-notif-b")

    user_a = next(iter(users_a.values()))
    user_b = next(iter(users_b.values()))
    db_session.add_all(
        [
            NotificationPreference(user_id=user_a.id, notification_type=NotificationType.EXCEPTION_CREATED, email_enabled=True),
            NotificationPreference(user_id=user_b.id, notification_type=NotificationType.EXCEPTION_CREATED, email_enabled=True),
        ]
    )
    db_session.commit()

    counts_a = _counts_dict(db_session, tenant_a.id)
    counts_b = _counts_dict(db_session, tenant_b.id)

    assert counts_a["notification_preference"] == 1
    assert counts_b["notification_preference"] == 1


def test_global_platform_tables_counted_in_full_regardless_of_tenant(db_session, tenant_factory):
    tenant_a, _account_a, _users_a = tenant_factory.make(slug="schema-summary-global-a")
    tenant_b, _account_b, _users_b = tenant_factory.make(slug="schema-summary-global-b")

    db_session.add(PlatformApiKey(name="Usage metering key", key_hash="a" * 64))
    db_session.commit()

    counts_a = _counts_dict(db_session, tenant_a.id)
    counts_b = _counts_dict(db_session, tenant_b.id)

    # payment_network is seeded once (2 rows: check, ach) by the shared `engine` fixture.
    assert counts_a["payment_network"] == 2
    assert counts_b["payment_network"] == 2
    assert counts_a["platform_api_key"] == 1
    assert counts_b["platform_api_key"] == 1
