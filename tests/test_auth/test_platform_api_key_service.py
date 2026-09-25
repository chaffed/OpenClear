# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.services import platform_api_key_service


def test_generate_and_create_returns_verifiable_key(db_session):
    row, raw_key = platform_api_key_service.generate_and_create(db_session, "Test Integration")
    db_session.commit()

    assert row.key_hash != raw_key  # never stored in plaintext
    assert raw_key.startswith("openclear_platform_")

    verified = platform_api_key_service.verify(db_session, raw_key)
    assert verified is not None
    assert verified.id == row.id
    assert verified.last_used_at is not None


def test_verify_rejects_unknown_key(db_session):
    assert platform_api_key_service.verify(db_session, "not-a-real-key") is None


def test_verify_rejects_revoked_key(db_session):
    row, raw_key = platform_api_key_service.generate_and_create(db_session, "Test Integration")
    db_session.commit()

    revoked = platform_api_key_service.revoke(db_session, row.id)
    db_session.commit()

    assert revoked is not None
    assert revoked.revoked_at is not None
    assert platform_api_key_service.verify(db_session, raw_key) is None


def test_revoke_unknown_key_returns_none(db_session):
    import uuid

    assert platform_api_key_service.revoke(db_session, uuid.uuid4()) is None


def test_revoke_already_revoked_key_returns_none(db_session):
    row, _raw_key = platform_api_key_service.generate_and_create(db_session, "Test Integration")
    db_session.commit()
    platform_api_key_service.revoke(db_session, row.id)
    db_session.commit()

    assert platform_api_key_service.revoke(db_session, row.id) is None
