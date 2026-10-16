# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""CUEC mappings and attestations under row-level security (CONTROLS_PLAN.md CT4, CT-D12): a bank
sees only its own, every bank reads the installation's, and only a session bound to no bank (the
platform's) can write one."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from openclear.db.tenancy import bind_session_to_tenant
from openclear.domain.cuec_attestation import CuecAttestation
from openclear.services import attestation_service
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs OPENCLEAR_TEST_DATABASE_URL (Postgres)")


def test_installation_rows_are_read_by_every_bank_and_written_by_none(app_session_factory, db_session, tenant_factory):
    mine, _account, users = tenant_factory.make(slug="rls-att-mine")
    other, _other_account, other_users = tenant_factory.make(slug="rls-att-other")
    attestation_service.add_mapping(db_session, other.id, None, "B-AC-5-01", control_ref="THEIRS-1")
    attestation_service.record_manual(db_session, other.id, None, "B-RA-3-01", start=date(2026, 1, 1), end=date(2026, 3, 31),
                                      result="operated", statement="Theirs.", user_id=other_users["admin"].id)
    db_session.commit()

    platform = app_session_factory()  # bound to no bank: the platform's
    try:
        host = attestation_service.record_installation(platform, "H-CP-9-01", start=date(2026, 1, 1), end=date(2026, 3, 31),
                                                       result="operated", statement="Restore tested.", user_id=users["admin"].id)
        platform.commit()
    finally:
        platform.close()

    bank = app_session_factory()
    try:
        bind_session_to_tenant(bank, mine.id)
        visible = bank.execute(select(CuecAttestation)).scalars().all()
        assert [a.id for a in visible] == [host.id]  # the installation's, not the other bank's
        assert attestation_service.mappings(bank, mine.id, None) == [] and attestation_service.mappings(bank, other.id, None) == []
        bank.add(CuecAttestation(tenant_id=None, cuec_id="H-CP-9-01", period_kind="custom", period_start=date(2026, 1, 1),
                                 period_end=date(2026, 3, 31), result="operated", method="manual", evidence={}, files=[], status="accepted",
                                 auto_accepted=False, inventory_version="1.0"))
        with pytest.raises(DBAPIError):
            bank.flush()  # a bank can't write the installation's
    finally:
        bank.rollback()
        bank.close()
