# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Secrets stored in the database (SSO client secrets, the SAML signing key, webhook
secrets, plugin settings marked secret) are encrypted with **AES-256-GCM**
(COMPLIANCE_PLAN.md C4, decision C-D5), as `v2:<key id>:<base64url(nonce || ciphertext || tag)>`.

The key is derived from Settings.sso_encryption_key with HKDF-SHA256, so the setting stays a
plain string like every other secret setting. The key id (a fingerprint of the derived key)
names which key encrypted a value, so the key can be **rotated**: put the old value in
sso_encryption_key_previous and a new one in sso_encryption_key, and values under either
still decrypt; reencrypt_all (run at startup) moves them to the new key.

Values written before 1.9 are Fernet (AES-128-CBC with HMAC-SHA256). They still decrypt, and
reencrypt_all upgrades them."""

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from openclear.config import Settings, get_settings

logger = logging.getLogger(__name__)

_VERSION = "v2"
_INFO = b"openclear field encryption v2"


class DecryptionError(ValueError):
    """No configured key opens this value."""


def _secrets(settings: Settings) -> list[str]:
    previous = getattr(settings, "sso_encryption_key_previous", None)
    return [settings.sso_encryption_key] + ([previous] if previous else [])


def _aes_key(secret: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(secret.encode("utf-8"))


def _key_id(key: bytes) -> str:
    return hashlib.sha256(b"key id:" + key).hexdigest()[:12]


def _fernet(secret: str) -> Fernet:
    """The pre-1.9 scheme: sha256 of the setting as a Fernet key."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


def current_key_id(settings: Settings | None = None) -> str:
    return _key_id(_aes_key((settings or get_settings()).sso_encryption_key))


def encrypt_secret(plaintext: str, *, settings: Settings | None = None) -> str:
    key = _aes_key((settings or get_settings()).sso_encryption_key)
    key_id = _key_id(key)
    nonce = os.urandom(12)
    sealed = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), f"{_VERSION}:{key_id}".encode())
    return f"{_VERSION}:{key_id}:" + base64.urlsafe_b64encode(nonce + sealed).decode("ascii").rstrip("=")


def decrypt_secret(ciphertext: str, *, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if ciphertext.startswith(f"{_VERSION}:"):
        _version, key_id, body = ciphertext.split(":", 2)
        for secret in _secrets(settings):
            key = _aes_key(secret)
            if _key_id(key) == key_id:
                raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
                return AESGCM(key).decrypt(raw[:12], raw[12:], f"{_VERSION}:{key_id}".encode()).decode("utf-8")
        raise DecryptionError(f"No configured encryption key has id {key_id}.")
    for secret in _secrets(settings):
        try:
            return _fernet(secret).decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken:
            continue
    raise DecryptionError("No configured encryption key opens this value.")


def legacy_encrypt(plaintext: str, *, settings: Settings | None = None) -> str:
    """The pre-1.9 Fernet format, for a database downgrade to a version that can't read
    AES-256-GCM values (migrations/versions/a8c0e2a4c6e9_control_run.py)."""
    return _fernet((settings or get_settings()).sso_encryption_key).encrypt(plaintext.encode("utf-8")).decode("ascii")


ENCRYPTED_COLUMNS = (  # (table, column), for migrations, which don't use the models
    ("sso_connection", "client_secret_encrypted"),
    ("sso_connection", "saml_sp_private_key_encrypted"),
    ("webhook_endpoint", "secret_encrypted"),
    ("tenant_extension", "config_encrypted"),
)


def is_current(ciphertext: str | None, *, settings: Settings | None = None) -> bool:
    """AES-256-GCM under the current key (nothing to re-encrypt)."""
    return ciphertext is None or ciphertext.startswith(f"{_VERSION}:{current_key_id(settings)}:")


def encrypted_columns():
    """Every column holding an encrypt_secret value: (model, attribute name)."""
    from openclear.domain.security_event_destination import SecurityEventDestination
    from openclear.domain.sso_connection import SsoConnection
    from openclear.domain.tenant_extension import TenantExtension
    from openclear.domain.webhook import WebhookEndpoint

    return [
        (SsoConnection, "client_secret_encrypted"),
        (SsoConnection, "saml_sp_private_key_encrypted"),
        (WebhookEndpoint, "secret_encrypted"),
        (TenantExtension, "config_encrypted"),
        (SecurityEventDestination, "token_encrypted"),
    ]


def inventory(session, tenant_id=None) -> dict[str, int]:
    """How many stored values are current, and how many still need re-encrypting (an older
    scheme or key): one bank's, or (tenant_id None) all those the session can see."""
    from sqlalchemy import select

    counts = {"current": 0, "outdated": 0}
    for model, attr in encrypted_columns():
        stmt = select(getattr(model, attr)).where(getattr(model, attr).is_not(None))
        if tenant_id is not None:
            stmt = stmt.where(model.tenant_id == tenant_id)
        for value in session.execute(stmt).scalars():
            counts["current" if is_current(value) else "outdated"] += 1
    return counts


def reencrypt_all(session) -> int:
    """Re-encrypts every stored value not yet AES-256-GCM under the current key, bank by bank
    (Postgres row-level security). Returns how many changed; the caller commits. A value no
    configured key opens is left alone and logged."""
    from sqlalchemy import select

    from openclear.db.tenancy import bind_session_to_tenant
    from openclear.domain.tenant import Tenant

    changed = 0
    for tenant_id in list(session.execute(select(Tenant.id)).scalars()):
        bind_session_to_tenant(session, tenant_id)
        for model, attr in encrypted_columns():
            column = getattr(model, attr)
            rows = session.execute(select(model).where(model.tenant_id == tenant_id, column.is_not(None))).scalars().all()
            for row in rows:
                value = getattr(row, attr)
                if is_current(value):
                    continue
                try:
                    setattr(row, attr, encrypt_secret(decrypt_secret(value)))
                    changed += 1
                except DecryptionError:
                    logger.error("Can't re-encrypt %s.%s for %s: no configured key opens it", model.__tablename__, attr, row.id)
        session.flush()
    return changed
