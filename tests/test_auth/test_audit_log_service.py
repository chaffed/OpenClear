# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid

from openclear.domain.audit_log_entry import AuditLogEntry
from openclear.services import audit_log_service


def _record(db_session, tenant, users, action="issued_item.create", summary="did a thing"):
    return audit_log_service.record_action(
        db_session,
        tenant.id,
        actor_user_id=users["admin"].id,
        channel="web",
        action=action,
        summary=summary,
        resource_type="issued_item",
        resource_id=uuid.uuid4(),
    )


def test_record_action_produces_a_verifiable_chain(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="audit-basic")
    entry = _record(db_session, tenant, users)
    db_session.commit()

    assert entry.prev_entry_hash is None  # first entry in the tenant's chain
    assert entry.entry_hash

    result = audit_log_service.verify_chain(db_session, tenant.id)
    assert result.valid is True
    assert result.checked_count == 1


def test_chain_links_successive_entries(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="audit-chain-link")
    first = _record(db_session, tenant, users, summary="first")
    db_session.commit()
    second = _record(db_session, tenant, users, summary="second")
    db_session.commit()

    assert second.prev_entry_hash == first.entry_hash

    result = audit_log_service.verify_chain(db_session, tenant.id)
    assert result.valid is True
    assert result.checked_count == 2


def test_verify_chain_survives_a_fresh_reload(db_session, tenant_factory):
    """Regression test: SQLite drops tzinfo on reload (a freshly-created datetime is
    tz-aware, the same row read back in a later request is naive) — without normalizing
    occurred_at before hashing, this would falsely report tampering on an untouched
    entry. expire_all() forces every attribute, including occurred_at, to be re-fetched
    from the DB rather than served from the identity map's already-aware Python object."""
    tenant, _account, users = tenant_factory.make(slug="audit-reload")
    _record(db_session, tenant, users)
    _record(db_session, tenant, users)
    db_session.commit()

    db_session.expire_all()
    result = audit_log_service.verify_chain(db_session, tenant.id)
    assert result.valid is True
    assert result.checked_count == 2


def test_verify_chain_detects_tampered_entry(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="audit-tamper")
    entry = _record(db_session, tenant, users, summary="original summary")
    _record(db_session, tenant, users, summary="second entry")
    db_session.commit()

    stored = db_session.get(AuditLogEntry, entry.id)
    stored.summary = "attacker-edited summary"
    db_session.commit()

    result = audit_log_service.verify_chain(db_session, tenant.id)
    assert result.valid is False
    assert result.broken_at_entry_id == entry.id
    assert result.checked_count == 0  # broke on the very first entry


def test_verify_chain_detects_deleted_entry(db_session, tenant_factory):
    """Deleting a row outright (not just editing it) is exactly the threat independent
    per-row signatures can't catch but a hash CHAIN can: the next surviving entry's
    prev_entry_hash still points at the now-missing row's hash, so the link breaks."""
    tenant, _account, users = tenant_factory.make(slug="audit-delete")
    first = _record(db_session, tenant, users, summary="first")
    db_session.commit()
    middle = _record(db_session, tenant, users, summary="middle")
    db_session.commit()
    last = _record(db_session, tenant, users, summary="last")
    db_session.commit()

    db_session.delete(db_session.get(AuditLogEntry, middle.id))
    db_session.commit()

    result = audit_log_service.verify_chain(db_session, tenant.id)
    assert result.valid is False
    assert result.broken_at_entry_id == last.id
    assert result.checked_count == 1  # only `first` verified cleanly before the gap


def test_chains_are_independent_per_tenant(db_session, tenant_factory):
    tenant_a, _account_a, users_a = tenant_factory.make(slug="audit-tenant-a")
    tenant_b, _account_b, users_b = tenant_factory.make(slug="audit-tenant-b")

    _record(db_session, tenant_a, users_a, summary="tenant a action")
    db_session.commit()
    entry_b = _record(db_session, tenant_b, users_b, summary="tenant b action")
    db_session.commit()

    # tenant b's chain starts fresh (null prev_hash) even though tenant a already has
    # entries — chains never cross tenant boundaries.
    assert entry_b.prev_entry_hash is None
    assert audit_log_service.verify_chain(db_session, tenant_a.id).checked_count == 1
    assert audit_log_service.verify_chain(db_session, tenant_b.id).checked_count == 1


def test_verify_chain_empty_tenant_is_valid(db_session, tenant_factory):
    tenant, _account, _users = tenant_factory.make(slug="audit-empty")
    result = audit_log_service.verify_chain(db_session, tenant.id)
    assert result.valid is True
    assert result.checked_count == 0
    assert result.broken_at_entry_id is None


def test_list_entries_returns_all_for_tenant(db_session, tenant_factory):
    tenant, _account, users = tenant_factory.make(slug="audit-list")
    _record(db_session, tenant, users, summary="one")
    _record(db_session, tenant, users, summary="two")
    db_session.commit()

    entries = audit_log_service.list_entries(db_session, tenant.id)
    assert len(entries) == 2


def test_a_writer_that_loses_a_race_retries_and_the_chain_stays_whole(db_session, tenant_factory, monkeypatch):
    """Forces the race deterministically, on any database: between the writer reading the
    latest entry and inserting its own, a competing entry takes the next sequence number.
    The unique (tenant_id, sequence) constraint rejects the stale insert, and the writer
    re-reads and chains onto the competitor instead of forking the chain."""
    tenant, _account, users = tenant_factory.make(slug="audit-race")
    _record(db_session, tenant, users, summary="first")
    db_session.commit()

    real_build = audit_log_service._build_entry
    calls = []

    def build_then_lose_the_race(session, tenant_id, **kwargs):
        stale = real_build(session, tenant_id, **kwargs)
        if not calls:
            competitor = real_build(session, tenant_id, **{**kwargs, "summary": "competitor"})
            with session.begin_nested():
                session.add(competitor)
        calls.append(stale)
        return stale

    monkeypatch.setattr(audit_log_service, "_build_entry", build_then_lose_the_race)
    entry = _record(db_session, tenant, users, summary="late writer")
    db_session.commit()

    assert len(calls) == 2  # the first attempt collided and was retried
    assert entry.sequence == 3
    entries = audit_log_service.list_entries(db_session, tenant.id, order_by=AuditLogEntry.sequence)
    assert [e.summary for e in entries] == ["first", "competitor", "late writer"]
    assert audit_log_service.verify_chain(db_session, tenant.id).valid is True


def test_sequence_numbers_are_per_bank(db_session, tenant_factory):
    tenant_a, _a, users_a = tenant_factory.make(slug="audit-seq-a")
    tenant_b, _b, users_b = tenant_factory.make(slug="audit-seq-b")
    first_a = _record(db_session, tenant_a, users_a)
    first_b = _record(db_session, tenant_b, users_b)
    second_a = _record(db_session, tenant_a, users_a)
    db_session.commit()

    assert (first_a.sequence, second_a.sequence, first_b.sequence) == (1, 2, 1)


def test_verify_chain_walks_across_batches(db_session, tenant_factory, monkeypatch):
    from openclear.repositories.audit_log_repo import AuditLogRepository

    tenant, _account, users = tenant_factory.make(slug="audit-batches")
    recorded = [_record(db_session, tenant, users, summary=f"entry {n}").id for n in range(7)]
    db_session.commit()

    walked = [e.id for e in AuditLogRepository(db_session, tenant.id).iter_in_chain_order(batch_size=3)]
    assert walked == recorded

    real_iter = AuditLogRepository.iter_in_chain_order
    monkeypatch.setattr(AuditLogRepository, "iter_in_chain_order", lambda self, batch_size=3: real_iter(self, batch_size))
    result = audit_log_service.verify_chain(db_session, tenant.id)
    assert (result.valid, result.checked_count) == (True, 7)
