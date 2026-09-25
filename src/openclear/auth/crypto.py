# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import base64
import hashlib

from cryptography.fernet import Fernet

from openclear.config import Settings, get_settings


def _fernet(settings: Settings | None = None) -> Fernet:
    """Settings.sso_encryption_key stays a plain string, same style as every other
    `*_secret_key` setting, rather than requiring a hand-generated base64 Fernet key —
    fed through sha256 into a valid 32-byte urlsafe-base64 key."""
    settings = settings or get_settings()
    digest = hashlib.sha256(settings.sso_encryption_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str, *, settings: Settings | None = None) -> str:
    return _fernet(settings).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, *, settings: Settings | None = None) -> str:
    return _fernet(settings).decrypt(ciphertext.encode("ascii")).decode("utf-8")
