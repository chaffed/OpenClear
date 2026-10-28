# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Custom fields under row-level security (plans/CUSTOM_FIELDS_PLAN.md CF-D9): a bank's session
reads its own definitions and values and none of another bank's; the provider's values on banks
have no tenant policy and are read only by the provider's code."""

import pytest
from sqlalchemy import select

from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.custom_field import CustomFieldValue, PlatformCustomFieldValue
from openclear.services import custom_field_service as cf
from openclear.services.custom_field_service import FieldInput
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_a_banks_session_sees_only_its_own_fields_and_values(app_session_factory, db_session, tenant_factory):
    bank_a, account_a, _u = tenant_factory.make(slug="rls-cf-a")
    bank_b, account_b, _v = tenant_factory.make(slug="rls-cf-b")
    for bank, account in ((bank_a, account_a), (bank_b, account_b)):
        cf.create_field(db_session, bank.id, "account", FieldInput(key="officer_code", label="Officer", field_type="text"), user_id=None)
        cf.set_values(db_session, bank.id, "account", account.id, {"officer_code": f"JS-{bank.slug[-1]}"}, user_id=None)
    cf.create_platform_field(db_session, FieldInput(key="erp_id", label="ERP id", field_type="text"), user_id=None)
    cf.set_platform_values(db_session, bank_a.id, {"erp_id": "ERP-A"}, user_id=None)
    db_session.commit()

    with app_session_factory() as session:
        bind_session_to_tenant(session, bank_a.id)
        assert [f.key for f in cf.list_fields(session, bank_a.id, "account")] == ["officer_code"]
        assert cf.values_of(session, bank_a.id, "account", account_a.id) == {"officer_code": "JS-a"}
        # Even asked for the other bank's rows by id, the policy returns none.
        assert session.execute(select(CustomFieldValue).where(CustomFieldValue.tenant_id == bank_b.id)).scalars().all() == []
        assert cf.values_of(session, bank_b.id, "account", account_b.id) == {}
        # The provider's values have no tenant policy: the table is readable, but only the
        # provider's code reads it, and a bank's pages ask for shown-to-bank values only.
        assert cf.platform_values_for(session, [bank_a.id], shown_to_bank_only=True) == {bank_a.id: {}}
        assert session.execute(select(PlatformCustomFieldValue.bank_id)).scalars().all() == [bank_a.id]
