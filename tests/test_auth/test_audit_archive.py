# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Archiving old audit entries into signed files (SCALE_PLAN.md S7): nothing is lost, the
chain still verifies end to end, tampering with an archive shows, and a broken chain is
never archived."""

import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from openclear import storage
from openclear.config import get_settings
from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.services import audit_archive_service, audit_log_service, housekeeping_service

# older_than_days=-1 puts the cutoff tomorrow: every entry but the newest is old enough.
EVERYTHING = -1


def _log(db_session, tenant, users, count: int, start: int = 0) -> None:
    for n in range(start, start + count):
        audit_log_service.record_action(
            db_session, tenant.id, actor_user_id=users["admin"].id, channel="web", action="test.action",
            summary=f"entry {n}", resource_type="test", resource_id=None, metadata={"n": n},
        )
        db_session.commit()


def _left(db_session, tenant) -> int:
    return db_session.scalar(select(func.count()).select_from(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id))


def test_archived_entries_stay_verifiable(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="audit-archive")
    _log(db_session, tenant, users, 10)
    before = _left(db_session, tenant)

    checkpoint = audit_archive_service.archive_old_entries(db_session, tenant.id, EVERYTHING)

    assert checkpoint.entry_count == before - 1 and _left(db_session, tenant) == 1  # the newest stays
    with storage.local_path(checkpoint.archive_ref) as path:
        lines = gzip.decompress(Path(path).read_bytes()).decode().splitlines()
    assert len(lines) == checkpoint.entry_count and json.loads(lines[-1])["entry_hash"] == checkpoint.through_entry_hash
    assert audit_log_service.verify_chain(db_session, tenant.id).valid
    assert audit_archive_service.verify_archives(db_session, tenant.id).valid

    # New entries chain on as usual; a second run makes a second archive that links up.
    _log(db_session, tenant, users, 3, start=10)
    second = audit_archive_service.archive_old_entries(db_session, tenant.id, EVERYTHING)
    assert second.first_sequence == checkpoint.through_sequence + 1 and second.entry_count == 3
    assert audit_log_service.verify_chain(db_session, tenant.id).valid
    check = audit_archive_service.verify_archives(db_session, tenant.id)
    assert (check.valid, check.checked_archives, check.checked_entries) == (True, 2, before + 2)


def test_nothing_old_enough_means_no_archive(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="audit-archive-none")
    _log(db_session, tenant, users, 3)
    assert audit_archive_service.archive_old_entries(db_session, tenant.id, 30) is None
    assert _left(db_session, tenant) >= 3


def test_a_changed_archive_fails_verification(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="audit-archive-tamper")
    _log(db_session, tenant, users, 5)
    checkpoint = audit_archive_service.archive_old_entries(db_session, tenant.id, EVERYTHING)

    path = Path(checkpoint.archive_ref)
    path.chmod(0o644)
    path.write_bytes(gzip.compress(gzip.decompress(path.read_bytes()).replace(b"entry 1", b"entry X")))

    check = audit_archive_service.verify_archives(db_session, tenant.id)
    assert not check.valid and "has changed" in check.problem


def test_a_broken_chain_is_never_archived(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="audit-archive-broken")
    _log(db_session, tenant, users, 5)
    entry = db_session.execute(select(AuditLogEntry).where(AuditLogEntry.tenant_id == tenant.id).order_by(AuditLogEntry.sequence).offset(2)).scalars().first()
    entry.summary = "edited after the fact"
    db_session.commit()
    before = _left(db_session, tenant)

    with pytest.raises(audit_archive_service.AuditChainBroken):
        audit_archive_service.archive_old_entries(db_session, tenant.id, EVERYTHING)
    db_session.rollback()
    assert _left(db_session, tenant) == before and audit_archive_service.list_checkpoints(db_session, tenant.id) == []


def test_housekeeping_archives_when_turned_on(db_session, tenant_factory, monkeypatch):
    tenant, _account, users = tenant_factory.make(slug="audit-archive-hk")
    _log(db_session, tenant, users, 4)
    monkeypatch.setattr(get_settings(), "audit_archive_after_days", 400)
    real = audit_archive_service.archive_all_banks
    seen = []

    def archive_with_test_cutoff(session, days):
        seen.append(days)
        return real(session, EVERYTHING)  # entries made a moment ago aren't 400 days old

    monkeypatch.setattr(audit_archive_service, "archive_all_banks", archive_with_test_cutoff)
    result = housekeeping_service.run(db_session)

    assert seen == [400]

    assert result.deleted["audit_entries_archived"] >= 3  # all but the newest
    assert audit_log_service.verify_chain(db_session, tenant.id).valid


def test_the_audit_log_page_lists_verifies_and_serves_archives(client, db_session, tenant_factory):
    from tests.conftest import TenantFactory

    tenant, _account, users = tenant_factory.make(slug="audit-archive-web")
    _log(db_session, tenant, users, 4)
    checkpoint = audit_archive_service.archive_old_entries(db_session, tenant.id, EVERYTHING)
    client.get("/ui/login")
    csrf = client.cookies.get("csrf_token")
    client.post("/ui/login", data={"tenant_slug": tenant.slug, "email": users["admin"].email, "password": TenantFactory.PASSWORD,
                                   "csrf_token": csrf, "next": "/ui/"})

    assert "Archived entries" in client.get("/ui/audit-log").text
    verified = client.get("/ui/audit-log/verify").text
    assert "chain verified" in verified and "1 archive verified" in verified
    download = client.get(f"/ui/audit-log/archives/{checkpoint.id}")
    assert download.status_code == 200 and gzip.decompress(download.content).count(b"\n") == checkpoint.entry_count
    assert client.get(f"/ui/audit-log/archives/{tenant.id}").status_code == 404
