# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from openclear.auth.keys import load_private_key, load_public_key
from openclear.config import get_settings


def content_hash(data: bytes) -> str:
    """A plain content fingerprint — informational, quotable in an audit report
    independent of the server's signing key. NOT the tamper-evidence mechanism itself
    (see sign_file/verify_signature for that); anyone who can edit the DB row could
    recompute and replace this alongside a tampered file."""
    return hashlib.sha256(data).hexdigest()


def sign_file(data: bytes) -> str:
    """ECDSA-SHA256 over the raw file bytes using a server-held private key
    (config.Settings.file_signing_private_key_path) — same key-pair pattern already used
    for JWTs (auth/security.py). Only someone with the private key can produce a
    signature verify_signature will accept, which is what makes this actual
    tamper-evidence rather than just a checksum."""
    private_key = load_private_key(get_settings().file_signing_private_key_path)
    signature = private_key.sign(data, ec.ECDSA(hashes.SHA256()))
    return signature.hex()


def verify_signature(data: bytes, signature: str) -> bool:
    """ECDSA signatures are non-deterministic (a fresh random nonce each time sign_file
    runs), so this can't recompute-and-compare like an HMAC would — it must verify the
    stored signature against the public key instead."""
    public_key = load_public_key(get_settings().file_signing_public_key_path)
    try:
        public_key.verify(bytes.fromhex(signature), data, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False
