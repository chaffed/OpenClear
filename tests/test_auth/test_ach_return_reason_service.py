# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.services import ach_return_reason_service
from openclear.services.ach_return_reason_service import (
    DEFAULT_ACH_RETURN_REASONS,
    AchReturnReasonInput,
    InvalidAchReturnReasonInput,
)


def test_new_tenant_gets_default_reasons_seeded(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="return-reason-defaults")

    reasons = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id)

    assert {r.reason_text for r in reasons} == {text for text, _code in DEFAULT_ACH_RETURN_REASONS}
    assert all(r.is_active for r in reasons)
    assert all(r.transaction_code is None for r in reasons)


def test_create_ach_return_reason_with_transaction_code(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="return-reason-create")

    reason = ach_return_reason_service.create_ach_return_reason(
        db_session, tenant.id, AchReturnReasonInput(reason_text="Duplicate Entry", transaction_code="667")
    )
    db_session.commit()

    assert reason.reason_text == "Duplicate Entry"
    assert reason.transaction_code == "667"
    assert reason.is_active is True


def test_create_ach_return_reason_rejects_blank_text(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="return-reason-blank")

    with pytest.raises(InvalidAchReturnReasonInput):
        ach_return_reason_service.create_ach_return_reason(
            db_session, tenant.id, AchReturnReasonInput(reason_text="   ", transaction_code=None)
        )


def test_create_ach_return_reason_rejects_non_digit_transaction_code(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="return-reason-bad-code")

    with pytest.raises(InvalidAchReturnReasonInput):
        ach_return_reason_service.create_ach_return_reason(
            db_session, tenant.id, AchReturnReasonInput(reason_text="Some Reason", transaction_code="abc")
        )


def test_update_ach_return_reason(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="return-reason-update")
    reason = ach_return_reason_service.create_ach_return_reason(
        db_session, tenant.id, AchReturnReasonInput(reason_text="Old Text", transaction_code=None)
    )
    db_session.commit()

    updated = ach_return_reason_service.update_ach_return_reason(
        db_session, tenant.id, reason.id, AchReturnReasonInput(reason_text="New Text", transaction_code="42")
    )
    db_session.commit()

    assert updated.reason_text == "New Text"
    assert updated.transaction_code == "42"


def test_deactivate_and_reactivate_ach_return_reason(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="return-reason-deactivate")
    reason = ach_return_reason_service.create_ach_return_reason(
        db_session, tenant.id, AchReturnReasonInput(reason_text="Temp Reason", transaction_code=None)
    )
    db_session.commit()

    ach_return_reason_service.deactivate_ach_return_reason(db_session, tenant.id, reason.id)
    db_session.commit()
    active_only = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)
    assert reason.id not in [r.id for r in active_only]

    ach_return_reason_service.reactivate_ach_return_reason(db_session, tenant.id, reason.id)
    db_session.commit()
    active_only = ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id, active_only=True)
    assert reason.id in [r.id for r in active_only]


def test_get_update_deactivate_return_none_for_unknown_id(db_session, tenant_factory):
    import uuid

    tenant, _account, _users = tenant_factory.make(slug="return-reason-unknown")
    unknown_id = uuid.uuid4()

    assert ach_return_reason_service.get_ach_return_reason(db_session, tenant.id, unknown_id) is None
    assert (
        ach_return_reason_service.update_ach_return_reason(
            db_session, tenant.id, unknown_id, AchReturnReasonInput(reason_text="X", transaction_code=None)
        )
        is None
    )
    assert ach_return_reason_service.deactivate_ach_return_reason(db_session, tenant.id, unknown_id) is None
