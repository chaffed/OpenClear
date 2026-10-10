# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Secrets stored in the database (COMPLIANCE_PLAN.md C4, decision C-D5; auth/crypto.py):
AES-256-GCM with a key id, older Fernet values still read and upgraded, and key rotation."""

import pytest

from openclear.auth import crypto
from openclear.config import get_settings


def _legacy(plaintext: str, secret: str) -> str:
    return crypto._fernet(secret).encrypt(plaintext.encode()).decode()


def test_values_are_aes_256_gcm_with_a_key_id():
    sealed = crypto.encrypt_secret("hunter2")
    version, key_id, body = sealed.split(":", 2)
    assert version == "v2" and key_id == crypto.current_key_id() and "hunter2" not in body
    assert crypto.decrypt_secret(sealed) == "hunter2"
    assert crypto.encrypt_secret("hunter2") != sealed  # a fresh nonce each time
    tampered = sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1]
    with pytest.raises(Exception):
        crypto.decrypt_secret(tampered)  # GCM authenticates


def test_older_fernet_values_still_read():
    assert crypto.decrypt_secret(_legacy("old secret", get_settings().sso_encryption_key)) == "old secret"
    assert not crypto.is_current(_legacy("x", get_settings().sso_encryption_key))


def test_rotating_the_key(monkeypatch):
    settings = get_settings()
    old_key = settings.sso_encryption_key
    under_old = crypto.encrypt_secret("rotate me")
    monkeypatch.setattr(settings, "sso_encryption_key", "a-brand-new-key-for-rotation-of-at-least-32")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt_secret(under_old)  # the old key isn't configured yet
    monkeypatch.setattr(settings, "sso_encryption_key_previous", old_key)
    assert crypto.decrypt_secret(under_old) == "rotate me" and not crypto.is_current(under_old)
    assert crypto.decrypt_secret(_legacy("legacy", old_key)) == "legacy"


def test_reencrypt_all_upgrades_what_is_stored(db_session, tenant_factory, monkeypatch):
    from openclear.services import webhook_service

    tenant, _account, users = tenant_factory.make(slug="crypto-upgrade")
    endpoint, secret = webhook_service.create_endpoint(db_session, tenant.id, url="https://hooks.example.com/oc",
                                                       event_types=["exception.created"])
    endpoint.secret_encrypted = _legacy(secret, get_settings().sso_encryption_key)
    db_session.commit()
    assert crypto.inventory(db_session)["outdated"] >= 1

    assert crypto.reencrypt_all(db_session) >= 1
    db_session.commit()
    assert crypto.is_current(endpoint.secret_encrypted) and crypto.decrypt_secret(endpoint.secret_encrypted) == secret
    assert crypto.inventory(db_session)["outdated"] == 0
    assert crypto.reencrypt_all(db_session) == 0  # nothing left to do
