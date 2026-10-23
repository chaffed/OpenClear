# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The ACH monitoring summary in the evidence export (REGULATORY_PLAN.md RG2, control OC-SI-10),
and the return reasons a business account's unauthorized debit needs (R29, R17)."""

from datetime import date, timedelta

from openclear.domain.decision import DecisionOutcome
from openclear.services import ach_return_reason_service, decision_service, evidence_service
from tests.test_web.test_web_exception_review import _ach_exception, _ctx


def test_the_summary_counts_debits_exceptions_and_returns_by_reason(db_session, tenant_factory):
    tenant, account, users = tenant_factory.make(slug="ach-monitoring")
    reasons = {r.transaction_code: r for r in ach_return_reason_service.list_ach_return_reasons(db_session, tenant.id)}
    assert {"R29", "R17"} <= set(reasons)  # a business account's unauthorized debit, and suspected fraud
    first = _ach_exception(db_session, tenant, account, trace="MON-1")
    _ach_exception(db_session, tenant, account, trace="MON-2")
    result = decision_service.decide(db_session, tenant.id, first.id, _ctx(tenant, users["admin"]), outcome=DecisionOutcome.RETURN,
                                     reason_code=reasons["R29"].reason_text, notes=None, ach_return_reason_id=reasons["R29"].id,
                                     channel="web")
    assert result.error is None
    db_session.commit()

    today = date.today()
    summary = evidence_service.build(db_session, tenant.id, start=today - timedelta(days=1), end=today + timedelta(days=1),
                                     generated_by_user_id=None)["ach_monitoring"]
    assert summary["debits_by_sec_code"] == {"WEB": 2} and summary["exceptions"] == 2 and summary["decisions"] == 1
    assert summary["returns_by_reason_code"] == {"R29": 1}
    assert summary["unauthorized_returns_by_originator"] == [{"originator_id": "ACMECORP", "originator_name": "Acme Corp", "returns": 1}]
    assert summary["minutes_to_decision"]["median"] is not None and "credits" in summary["scope"]
