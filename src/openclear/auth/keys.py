# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Shared PEM key loading for the three ECDSA P-256 key pairs this app signs with (JWTs
— auth/security.py; bulk-upload files — bulk_import/signing.py; the audit log hash
chain — services/audit_log_service.py). One place for "read a PEM file off disk and
parse it, with a clear error if that fails" rather than three copies of the same few
lines. Cached by path — these are read from disk once per process, not on every
sign/verify call, same reasoning as config.get_settings()'s own lru_cache."""

from functools import lru_cache

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key


class KeyLoadError(Exception):
    """Raised when a configured key path is missing or doesn't contain a valid PEM key
    — always a deployment/configuration problem, never a per-request condition, so
    callers let this propagate rather than catching it."""


@lru_cache
def load_private_key(path: str) -> ec.EllipticCurvePrivateKey:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise KeyLoadError(
            f"Couldn't read private key at {path!r}: {exc}. Generate one with "
            "`python scripts/generate_keys.py` — see README.md's 'Signing keys' section."
        ) from exc
    key = load_pem_private_key(data, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise KeyLoadError(f"{path!r} is not an EC private key (expected P-256/ECDSA).")
    return key


@lru_cache
def load_public_key(path: str) -> ec.EllipticCurvePublicKey:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise KeyLoadError(
            f"Couldn't read public key at {path!r}: {exc}. Generate one with "
            "`python scripts/generate_keys.py` — see README.md's 'Signing keys' section."
        ) from exc
    key = load_pem_public_key(data)
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise KeyLoadError(f"{path!r} is not an EC public key (expected P-256/ECDSA).")
    return key
