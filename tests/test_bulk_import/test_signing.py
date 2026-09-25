# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.bulk_import.signing import content_hash, sign_file, verify_signature


def test_sign_and_verify_roundtrip():
    data = b"account_number,check_number,amount\n1001,5001,150.00\n"
    signature = sign_file(data)
    assert verify_signature(data, signature) is True


def test_verify_fails_when_bytes_change_after_signing():
    data = bytearray(b"account_number,check_number,amount\n1001,5001,150.00\n")
    signature = sign_file(bytes(data))

    data[0] ^= 0xFF  # flip a single byte, simulating tampering after upload
    assert verify_signature(bytes(data), signature) is False


def test_content_hash_changes_with_any_byte_difference():
    a = b"hello world"
    b = b"hello worlds"
    assert content_hash(a) != content_hash(b)
    assert content_hash(a) == content_hash(a)


def test_signature_is_not_a_plain_hash():
    # sign_file must depend on the secret, not just be sha256(data) — otherwise it's
    # indistinguishable from content_hash and provides no real tamper-evidence.
    data = b"some bytes"
    assert sign_file(data) != content_hash(data)
