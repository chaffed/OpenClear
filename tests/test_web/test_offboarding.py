# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Offboarding a bank (plans/OFFBOARDING_PLAN.md): telling it, the wait, two operators for each
step, and deleting its data with a signed certificate; and each safeguard that stops a deletion."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from openclear.domain.account import Account
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.domain.bank_deletion import BankDeletion
from openclear.domain.data_export_job import DataExportJob, DataExportJobStatus
from openclear.domain.incident import IncidentNoticeDelivery
from openclear.domain.platform_action_entry import PlatformActionEntry
from openclear.domain.security_group import SecurityGroup
from openclear.domain.tenant import Tenant
from openclear.domain.tenant_membership import TenantMembership
from openclear.domain.user import User
from openclear.services import change_request_service, evidence_service, offboarding_service
from openclear.services.offboarding_service import OffboardingError
from tests.test_web.test_staff_and_operator import _platform_login, _staff


def _operators(db_session):
    return _staff(db_session, "ops1@offboard.example", "operator"), _staff(db_session, "ops2@offboard.example", "operator")


def _approve(db_session, kind, payload, requester, approver, summary="test"):
    outcome = change_request_service.request_or_apply(db_session, kind, payload, tenant_id=None, user_id=requester.id, summary=summary)
    db_session.commit()
    if outcome.applied:
        return
    [change] = [c for c in change_request_service.pending(db_session, None) if c.kind == kind]
    change_request_service.decide(db_session, None, change.id, approve=True, user_id=approver.id)
    db_session.commit()


def _start_and_wait(db_session, bank, ops, *, exported=True):
    _approve(db_session, "platform.bank.offboard", {"bank_id": str(bank.id), "offboarding": True}, *ops)
    db_session.expire_all()
    bank = db_session.get(Tenant, bank.id)
    bank.offboarding_started_at = datetime.now(timezone.utc) - timedelta(days=31)
    if exported:
        admin = db_session.execute(select(TenantMembership.user_id).where(TenantMembership.tenant_id == bank.id)).scalars().first()
        db_session.add(DataExportJob(tenant_id=bank.id, requested_by_user_id=admin, status=DataExportJobStatus.COMPLETED,
                                     completed_at=datetime.now(timezone.utc) - timedelta(days=2)))
    db_session.commit()
    return bank


def test_a_bank_is_told_waits_and_its_data_is_deleted_with_a_certificate(db_session, tenant_factory):
    bank, _account, users = tenant_factory.make(slug="leaving")
    other, other_account, _u = tenant_factory.make(slug="staying")
    # Someone who also works at another bank keeps their sign-in.
    group = db_session.execute(select(SecurityGroup).where(SecurityGroup.tenant_id == other.id)).scalars().first()
    db_session.add(TenantMembership(user_id=users["viewer"].id, tenant_id=other.id, security_group_id=group.id))
    db_session.commit()
    ops = _operators(db_session)

    bank = _start_and_wait(db_session, bank, ops)
    notice = db_session.execute(select(IncidentNoticeDelivery).where(IncidentNoticeDelivery.tenant_id == bank.id)).scalar_one()
    assert notice.recipients  # the bank was told, by email and on every administrator's page

    preview = offboarding_service.preview(db_session, bank.id)
    assert preview.rows["account"] >= 1 and users["admin"].email in preview.people_deleted
    assert users["viewer"].email in preview.people_kept and preview.final_export_at is not None

    _approve(db_session, "platform.bank.delete", {"bank_id": str(bank.id), "confirm_slug": "leaving", "requested_by": str(ops[0].id)}, *ops)
    record = db_session.execute(select(BankDeletion).where(BankDeletion.bank_id == bank.id)).scalar_one()
    runs_at = record.runs_at.replace(tzinfo=timezone.utc) if record.runs_at.tzinfo is None else record.runs_at  # SQLite drops the zone
    assert record.status == "queued" and runs_at > datetime.now(timezone.utc) + timedelta(hours=23)

    offboarding_service.run(db_session, record.id)
    db_session.commit()
    db_session.expire_all()

    bank = db_session.get(Tenant, bank.id)
    assert bank.data_deleted_at is not None and not bank.is_active and bank.slug == "leaving"
    for model in (Account, AuditLogEntry, TenantMembership):
        assert db_session.execute(select(func.count()).select_from(model).where(model.tenant_id == bank.id)).scalar_one() == 0
    assert db_session.execute(select(User).where(User.email == users["admin"].email)).scalar_one_or_none() is None
    assert db_session.get(User, users["viewer"].id) is not None  # belongs to another bank
    assert db_session.get(Account, other_account.id) is not None  # the other bank untouched

    record = db_session.get(BankDeletion, record.id)
    assert record.status == "done" and evidence_service.verify(record.certificate)
    certificate = record.certificate["bundle"]
    assert certificate["bank"]["slug"] == "leaving" and certificate["rows_deleted"]["account"] >= 1
    assert certificate["deletion_requested_by"] == ops[0].email and certificate["deletion_approved_by"] == ops[1].email
    assert certificate["action_log"]["entries"] > 0 and certificate["people_kept_because_they_belong_elsewhere"] == 1
    assert "backups" in certificate and certificate["final_data_export_completed_at"]
    actions = set(db_session.execute(select(PlatformActionEntry.action)).scalars())
    assert {"platform.bank.offboard.start", "platform.bank.delete.approve", "platform.bank.delete.done"} <= actions


def test_deletion_is_refused_until_every_safeguard_is_met(db_session, tenant_factory):
    bank, _account, _users = tenant_factory.make(slug="guarded")
    ops = _operators(db_session)
    delete = {"bank_id": str(bank.id), "confirm_slug": "guarded"}

    with pytest.raises(change_request_service.ChangeRequestError, match="Start offboarding"):
        _approve(db_session, "platform.bank.delete", delete, *ops)
    _approve(db_session, "platform.bank.offboard", {"bank_id": str(bank.id), "offboarding": True}, *ops)
    with pytest.raises(change_request_service.ChangeRequestError, match="can be deleted from"):
        _approve(db_session, "platform.bank.delete", delete, *ops)  # the wait isn't over

    bank = _start_and_wait(db_session, bank, ops, exported=False)
    with pytest.raises(change_request_service.ChangeRequestError, match="hasn't taken a full data export"):
        _approve(db_session, "platform.bank.delete", delete, *ops)
    with pytest.raises(change_request_service.ChangeRequestError, match="Type the bank's slug"):
        _approve(db_session, "platform.bank.delete", {**delete, "confirm_slug": "wrong", "override_reason": "Declined in writing"}, *ops)

    # A hold, put on by one operator, stops it; taking it off needs a second.
    _approve(db_session, "platform.bank.hold", {"bank_id": str(bank.id), "held": True, "reason": "Regulator's request"}, ops[0], ops[0])
    with pytest.raises(change_request_service.ChangeRequestError, match="legal hold"):
        _approve(db_session, "platform.bank.delete", {**delete, "override_reason": "Declined in writing"}, *ops)
    change_request_service.request_or_apply(db_session, "platform.bank.hold", {"bank_id": str(bank.id), "held": False},
                                            tenant_id=None, user_id=ops[0].id)
    db_session.commit()
    assert db_session.get(Tenant, bank.id).legal_hold_reason  # still on, waiting for the second operator


def test_a_hold_or_stopping_offboarding_during_the_delay_stops_the_deletion(db_session, tenant_factory):
    bank, _account, _users = tenant_factory.make(slug="reprieve")
    ops = _operators(db_session)
    bank = _start_and_wait(db_session, bank, ops)
    _approve(db_session, "platform.bank.delete", {"bank_id": str(bank.id), "confirm_slug": "reprieve"}, *ops)
    record = db_session.execute(select(BankDeletion).where(BankDeletion.bank_id == bank.id)).scalar_one()

    offboarding_service.set_hold(db_session, bank.id, True, reason="Litigation", user_id=ops[0].id)
    offboarding_service.run(db_session, record.id)
    db_session.commit()
    assert db_session.get(BankDeletion, record.id).status == "refused"
    assert db_session.get(Tenant, bank.id).data_deleted_at is None

    offboarding_service.set_hold(db_session, bank.id, False, reason=None, user_id=ops[1].id)
    _approve(db_session, "platform.bank.delete", {"bank_id": str(bank.id), "confirm_slug": "reprieve"}, *ops)
    queued = db_session.execute(select(BankDeletion).where(BankDeletion.bank_id == bank.id, BankDeletion.status == "queued")).scalar_one()
    _approve(db_session, "platform.bank.offboard", {"bank_id": str(bank.id), "offboarding": False}, ops[0], ops[0])  # one operator
    db_session.expire_all()
    assert db_session.get(BankDeletion, queued.id).status == "cancelled"
    offboarding_service.run(db_session, queued.id)
    assert db_session.get(Tenant, bank.id).data_deleted_at is None


def test_the_demo_bank_and_sandboxes_are_never_offboarded(db_session, tenant_factory):
    bank, _account, _users = tenant_factory.make(slug="sandboxed")
    bank.is_sandbox = True
    db_session.commit()
    with pytest.raises(OffboardingError, match="sandboxes"):
        offboarding_service.start(db_session, bank.id, user_id=None)


def test_the_offboarding_page_is_for_operators(client, db_session, tenant_factory):
    bank, _account, _users = tenant_factory.make(slug="paged")
    ops = _operators(db_session)
    assert client.get(f"/ui/platform/banks/{bank.id}/offboarding", follow_redirects=False).status_code in (302, 303, 401, 403)
    _platform_login(client, ops[0].email)
    page = client.get(f"/ui/platform/banks/{bank.id}/offboarding")
    assert page.status_code == 200 and "Ask to start offboarding" in page.text
    sent = client.post(f"/ui/platform/banks/{bank.id}/offboarding", data={"csrf_token": client.cookies.get("csrf_token"), "step": "start"},
                       follow_redirects=False)
    assert sent.status_code == 303 and "/ui/platform/changes" in sent.headers["location"]
    assert db_session.get(Tenant, bank.id).offboarding_started_at is None  # waiting for the second operator
    assert client.get(f"/ui/platform/banks/{uuid.uuid4()}/deletion-certificate", follow_redirects=False).status_code == 303
