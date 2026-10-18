# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Generates cryptographically valid WebAuthn ceremony responses without a browser.

There's no way to drive a real navigator.credentials.create()/get() call from a backend
test suite — WebAuthn is inherently browser-mediated. Instead, this hand-constructs the
same wire format a real authenticator would produce (CBOR attestation/authenticator data,
a COSE-encoded EC public key, a DER ECDSA signature over authData+clientDataHash) using
raw `cryptography` primitives, so our server-side verification code gets exercised
end-to-end rather than mocked out."""

import hashlib
import json
import os

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from webauthn.helpers import bytes_to_base64url, encode_cbor


class FakeAuthenticator:
    def __init__(self, rp_id: str, origin: str):
        self.rp_id = rp_id
        self.origin = origin
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0

    def _rp_id_hash(self) -> bytes:
        return hashlib.sha256(self.rp_id.encode("utf-8")).digest()

    def _cose_public_key(self) -> bytes:
        numbers = self.private_key.public_key().public_numbers()
        cose_key = {
            1: 2,  # kty: EC2
            3: -7,  # alg: ES256
            -1: 1,  # crv: P-256
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        }
        return encode_cbor(cose_key)

    def _client_data_json(self, challenge: bytes, ceremony_type: str) -> bytes:
        data = {"type": ceremony_type, "challenge": bytes_to_base64url(challenge), "origin": self.origin}
        return json.dumps(data).encode("utf-8")

    def create_registration_credential(self, challenge: bytes) -> dict:
        client_data_json = self._client_data_json(challenge, "webauthn.create")

        flags = 0x41  # bit 0: user present, bit 6: attested credential data included
        attested_credential_data = (
            b"\x00" * 16  # aaguid (zeroed — no meaning for a synthetic test authenticator)
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + self._cose_public_key()
        )
        auth_data = self._rp_id_hash() + bytes([flags]) + (0).to_bytes(4, "big") + attested_credential_data
        attestation_object = encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})

        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data_json),
                "attestationObject": bytes_to_base64url(attestation_object),
            },
        }

    def create_authentication_credential(self, challenge: bytes) -> dict:
        self.sign_count += 1
        client_data_json = self._client_data_json(challenge, "webauthn.get")

        flags = 0x01  # user present, no attested credential data on subsequent assertions
        auth_data = self._rp_id_hash() + bytes([flags]) + self.sign_count.to_bytes(4, "big")

        client_data_hash = hashlib.sha256(client_data_json).digest()
        signature = self.private_key.sign(auth_data + client_data_hash, ec.ECDSA(SHA256()))

        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data_json),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
            },
        }


# Platform staff's authenticators, by email, so a second sign-in in the same test uses the key
# the first registered (web/routers/platform.py: a key always follows the password).
_PLATFORM_AUTHENTICATORS: dict[str, "FakeAuthenticator"] = {}


def platform_sign_in(client, email: str, password: str):
    """Signs in to the platform pages: the password, then the security key (registering one the
    first time). Returns the password step's response and the CSRF token; when the password is
    refused, stops there."""
    import webauthn as _webauthn

    from openclear.config import get_settings

    client.get("/ui/platform/login")
    csrf = client.cookies.get("csrf_token")
    resp = client.post("/ui/platform/login", data={"email": email, "password": password, "csrf_token": csrf},
                       follow_redirects=False)
    if resp.status_code != 303:
        return resp, csrf
    settings = get_settings()
    fake = _PLATFORM_AUTHENTICATORS.setdefault(email, FakeAuthenticator(settings.webauthn_rp_id, settings.webauthn_origin))
    headers = {"X-CSRF-Token": csrf}
    if resp.headers["location"].endswith("/key/setup"):
        options = _webauthn.helpers.parse_registration_options_json(
            client.post("/ui/platform/login/key/setup/options", headers=headers).text)
        done = client.post("/ui/platform/login/key/setup/verify", headers=headers,
                           json={"credential": fake.create_registration_credential(options.challenge)})
    else:
        options = _webauthn.helpers.parse_authentication_options_json(
            client.post("/ui/platform/login/key/options", headers=headers).text)
        done = client.post("/ui/platform/login/key/verify", headers=headers,
                           json={"credential": fake.create_authentication_credential(options.challenge)})
    assert done.status_code == 200, done.text
    return resp, client.cookies.get("csrf_token")
