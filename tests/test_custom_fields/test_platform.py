# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The provider's fields on banks (plans/CUSTOM_FIELDS_PLAN.md CF3): defined by two operators,
values set by one, shown to a bank only when marked so, and kept when the bank is offboarded."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from openclear.domain.bank_deletion import BankDeletion
from openclear.domain.change_request import ChangeRequest
from openclear.domain.custom_field import PlatformCustomField, PlatformCustomFieldValue
from openclear.domain.data_export_job import DataExportJob, DataExportJobStatus
from openclear.domain.tenant import Tenant
from openclear.services import change_request_service, custom_field_service as cf, offboarding_service
from openclear.services.custom_field_service import FieldInput
from tests.test_web.test_staff_and_operator import _login, _platform_login, _staff


def test_two_operators_define_a_field_and_one_sets_a_banks_value(client, db_session, tenant_factory):
    bank, _account, users = tenant_factory.make(slug="cfp-bank")
    first = _staff(db_session, "ops1@cfp.example", "operator")
    second = _staff(db_session, "ops2@cfp.example", "operator")

    _platform_login(client, first.email)
    page = client.get("/ui/platform/custom-fields")
    assert page.status_code == 200 and "None yet" in page.text
    csrf = client.cookies.get("csrf_token")
    asked = client.post("/ui/platform/custom-fields", data={"csrf_token": csrf, "key": "erp_id", "label": "ERP id", "field_type": "text",
                                                            "unique": "1"}, follow_redirects=False)
    assert asked.status_code == 303 and "/ui/platform/changes" in asked.headers["location"]
    assert db_session.execute(select(PlatformCustomField)).scalar_one_or_none() is None  # not until a second operator
    bad = client.post("/ui/platform/custom-fields", data={"csrf_token": csrf, "key": "Bad", "label": "x", "field_type": "text"})
    assert bad.status_code == 422 and "A key is 1 to 40 characters" in bad.text

    change = db_session.execute(select(ChangeRequest).where(ChangeRequest.kind == "platform.custom_field.save")).scalar_one()
    change_request_service.decide(db_session, None, change.id, approve=True, user_id=second.id)
    db_session.commit()
    definition = db_session.execute(select(PlatformCustomField)).scalar_one()
    assert definition.key == "erp_id" and definition.unique and not definition.shown_to_bank

    # One operator sets a bank's value; it's in the provider's log, and the bank doesn't see it.
    saved = client.post(f"/ui/platform/banks/{bank.id}/fields", data={"csrf_token": csrf, "cf_erp_id": "ERP-100"}, follow_redirects=False)
    assert saved.status_code == 303
    assert cf.platform_values_for(db_session, [bank.id]) == {bank.id: {"erp_id": "ERP-100"}}
    assert "ERP-100" in client.get("/ui/platform/custom-fields").text
    client.cookies.clear()
    _login(client, bank.slug, users["admin"].email)
    assert "From your service provider" not in client.get("/ui/settings").text
    assert "ERP-100" not in client.get("/ui/settings/provider-fields").text

    # Shown to the bank: read-only in its Settings.
    cf.update_platform_field(db_session, definition.id, FieldInput(key="erp_id", label="ERP id", field_type="text", unique=True, shown_to_bank=True), user_id=None)
    db_session.commit()
    assert "From your service provider" in client.get("/ui/settings").text
    shown = client.get("/ui/settings/provider-fields").text
    assert "ERP-100" in shown and "<form" not in shown.split("provider-fields")[-1][:2000]


def test_the_providers_values_survive_a_banks_offboarding(db_session, tenant_factory):
    bank, _account, users = tenant_factory.make(slug="cfp-leaving")
    cf.create_platform_field(db_session, FieldInput(key="erp_id", label="ERP id", field_type="text"), user_id=None)
    cf.set_platform_values(db_session, bank.id, {"erp_id": "ERP-7"}, user_id=None)
    bank.offboarding_started_at = datetime.now(timezone.utc) - timedelta(days=31)
    db_session.add(DataExportJob(tenant_id=bank.id, requested_by_user_id=users["admin"].id, status=DataExportJobStatus.COMPLETED,
                                 completed_at=datetime.now(timezone.utc) - timedelta(days=1)))
    record = BankDeletion(bank_id=bank.id, status="queued", requested_at=datetime.now(timezone.utc))
    db_session.add(record)
    db_session.commit()

    offboarding_service.run(db_session, record.id)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Tenant, bank.id).data_deleted_at is not None
    assert db_session.execute(select(PlatformCustomFieldValue.text_value).where(PlatformCustomFieldValue.bank_id == bank.id)).scalar_one() == "ERP-7"
    assert "custom-field values" in db_session.get(BankDeletion, record.id).certificate["bundle"]["kept"]
