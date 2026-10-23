# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Deleting a leaving bank's data under row-level security, as the worker's restricted role runs
it (plans/OFFBOARDING_PLAN.md): every row with the bank's tenant_id goes, and no other bank's."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from openclear.db.base import Base
from openclear.domain.bank_deletion import BankDeletion
from openclear.domain.data_export_job import DataExportJob, DataExportJobStatus
from openclear.domain.tenant import Tenant
from openclear.services import offboarding_service
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def _rows(session, tenant_id) -> int:
    return sum(session.execute(select(func.count()).select_from(table).where(table.c.tenant_id == tenant_id)).scalar_one()
               for table in Base.metadata.sorted_tables if "tenant_id" in table.columns and table.name != "tenant")


def test_the_deletion_job_removes_every_row_of_the_bank_and_no_other(app_session_factory, db_session, tenant_factory):
    bank, _account, users = tenant_factory.make(slug="rls-leaving")
    other, _other_account, _u = tenant_factory.make(slug="rls-staying")
    bank.offboarding_started_at = datetime.now(timezone.utc) - timedelta(days=31)
    db_session.add(DataExportJob(tenant_id=bank.id, requested_by_user_id=users["admin"].id, status=DataExportJobStatus.COMPLETED,
                                 completed_at=datetime.now(timezone.utc) - timedelta(days=1)))
    record = BankDeletion(bank_id=bank.id, status="queued", requested_at=datetime.now(timezone.utc))
    db_session.add(record)
    db_session.commit()
    other_rows = _rows(db_session, other.id)
    assert _rows(db_session, bank.id) > 0

    with app_session_factory() as session:
        done = offboarding_service.run(session, record.id)
        assert done.status == "done", done.last_error
        session.commit()

    db_session.expire_all()
    assert _rows(db_session, bank.id) == 0
    assert _rows(db_session, other.id) == other_rows
    assert db_session.get(Tenant, bank.id).data_deleted_at is not None
