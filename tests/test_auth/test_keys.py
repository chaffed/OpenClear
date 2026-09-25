# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from openclear.auth.keys import KeyLoadError, load_private_key, load_public_key


def test_load_private_and_public_key_round_trip():
    private_key = load_private_key("dev_keys/jwt_private.pem")
    public_key = load_public_key("dev_keys/jwt_public.pem")

    signature = private_key.sign(b"hello", ec.ECDSA(hashes.SHA256()))
    public_key.verify(signature, b"hello", ec.ECDSA(hashes.SHA256()))  # raises if invalid


def test_load_private_key_is_cached_by_path():
    assert load_private_key("dev_keys/jwt_private.pem") is load_private_key("dev_keys/jwt_private.pem")


def test_load_private_key_missing_file_raises_clear_error():
    with pytest.raises(KeyLoadError, match="generate_keys.py"):
        load_private_key("dev_keys/does_not_exist.pem")


def test_load_public_key_missing_file_raises_clear_error():
    with pytest.raises(KeyLoadError, match="generate_keys.py"):
        load_public_key("dev_keys/does_not_exist.pem")


def test_different_key_pairs_are_not_interchangeable():
    # jwt and file_signing are deliberately separate key pairs — signing with one and
    # verifying with the other's public key must fail, not succeed.
    jwt_private = load_private_key("dev_keys/jwt_private.pem")
    file_signing_public = load_public_key("dev_keys/file_signing_public.pem")

    signature = jwt_private.sign(b"hello", ec.ECDSA(hashes.SHA256()))
    with pytest.raises(Exception):
        file_signing_public.verify(signature, b"hello", ec.ECDSA(hashes.SHA256()))
