# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""SAML 2.0 sign-on (SDK_PLAN.md phase F), beside OIDC (auth/oidc_service.py), using
python3-saml for the protocol and XML signatures.

OpenClear is the service provider. Per connection:
- **Metadata** at /ui/sso/saml/{tenant}/{connection}/metadata, with OpenClear's own
  certificate for the connection (generated when it's created) for the identity provider
  to verify our requests and encrypt assertions to us.
- **Sign-in** sends a signed AuthnRequest (HTTP-Redirect); the answer is POSTed to the
  assertion consumer service, /ui/sso/saml/{tenant}/{connection}/acs. The bank and
  connection are in the URL because the identity provider's POST is cross-site, before any
  session exists.
- **What's required of an answer:** a signed assertion (a signed message alone isn't
  enough), our entity ID as audience, our ACS as destination and recipient, within its
  validity window, SHA-256 or better, and, unless sign-in started at the identity provider
  (off by default per connection), answering the request we sent. Each assertion is
  accepted once (services/sso_service.record_assertion).
- **Single logout** at /ui/sso/saml/{tenant}/{connection}/slo: a signed LogoutRequest
  from the identity provider ends the person's OpenClear sessions."""

import datetime as dt
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.authn_request import OneLogin_Saml2_Authn_Request
from onelogin.saml2.constants import OneLogin_Saml2_Constants
from onelogin.saml2.settings import OneLogin_Saml2_Settings

from openclear.auth.crypto import decrypt_secret, encrypt_secret
from openclear.config import get_settings
from openclear.domain.sso_connection import SsoConnection

_SIG = OneLogin_Saml2_Constants.RSA_SHA256
_DIGEST = OneLogin_Saml2_Constants.SHA256


class SamlError(Exception):
    """The SAML exchange failed; the message is for the log, not the sign-in page."""


@dataclass(frozen=True, slots=True)
class SamlAssertion:
    email: str
    subject: str  # the NameID
    groups: list[str] = field(default_factory=list)
    assertion_id: str = ""
    expires_at: dt.datetime | None = None
    session_index: str | None = None
    in_response_to: str | None = None
    customer_number: str | None = None
    authentication: tuple[str, ...] = ()  # the AuthnContextClassRef values


# --- Keys ------------------------------------------------------------------------------


def generate_sp_keypair(connection_id: uuid.UUID) -> tuple[str, str]:
    """A self-signed RSA-3072 certificate and key for one connection (128-bit security, ASVS 5.0
    11.2.3; connections made before 1.22.1 keep their RSA-2048 keys), valid ten years:
    (certificate PEM, encrypted private key)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"OpenClear SAML {connection_id}")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650)).sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return cert.public_bytes(serialization.Encoding.PEM).decode(), encrypt_secret(key_pem)


def _pem_body(pem: str | None) -> str:
    """python3-saml wants the base64 body; accept a PEM or a bare body."""
    lines = [line.strip() for line in (pem or "").strip().splitlines()]
    return "".join(line for line in lines if line and not line.startswith("-----"))


# --- Settings --------------------------------------------------------------------------


def base_url(request_base: str) -> str:
    return (get_settings().public_base_url or request_base).rstrip("/")


def sp_urls(base: str, connection: SsoConnection) -> dict[str, str]:
    root = f"{base}/ui/sso/saml/{connection.tenant_id}/{connection.id}"
    return {"entity_id": f"{root}/metadata", "acs": f"{root}/acs", "slo": f"{root}/slo"}


def _settings(connection: SsoConnection, base: str) -> dict:
    urls = sp_urls(base, connection)
    idp: dict = {
        "entityId": connection.issuer,
        "singleSignOnService": {"url": connection.saml_sso_url or "", "binding": OneLogin_Saml2_Constants.BINDING_HTTP_REDIRECT},
        "x509cert": _pem_body(connection.saml_idp_certificate),
    }
    if connection.saml_slo_url:
        idp["singleLogoutService"] = {"url": connection.saml_slo_url, "binding": OneLogin_Saml2_Constants.BINDING_HTTP_REDIRECT}
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": urls["entity_id"],
            "assertionConsumerService": {"url": urls["acs"], "binding": OneLogin_Saml2_Constants.BINDING_HTTP_POST},
            "singleLogoutService": {"url": urls["slo"], "binding": OneLogin_Saml2_Constants.BINDING_HTTP_REDIRECT},
            "NameIDFormat": OneLogin_Saml2_Constants.NAMEID_UNSPECIFIED,
            "x509cert": _pem_body(connection.saml_sp_certificate),
            "privateKey": _pem_body(decrypt_secret(connection.saml_sp_private_key_encrypted)) if connection.saml_sp_private_key_encrypted else "",
        },
        "idp": idp,
        "security": {
            "authnRequestsSigned": True,
            "logoutRequestSigned": True,
            "logoutResponseSigned": True,
            "wantMessagesSigned": False,  # many providers sign only the assertion, which is required:
            "wantAssertionsSigned": True,
            "wantAssertionsEncrypted": False,  # accepted when encrypted, not required
            "wantNameId": True,
            "requestedAuthnContext": False,
            "signatureAlgorithm": _SIG,
            "digestAlgorithm": _DIGEST,
            "rejectDeprecatedAlgorithm": True,
            "allowRepeatAttributeName": True,
            "wantAttributeStatement": False,
        },
    }


def _request_data(base: str, path: str, *, get: dict | None = None, post: dict | None = None) -> dict:
    """What python3-saml compares the Destination with: the public URL, not whatever host
    a proxy forwarded to."""
    parts = urlsplit(base)
    https = parts.scheme == "https"
    return {
        "https": "on" if https else "off",
        "http_host": parts.hostname or "",
        "server_port": str(parts.port or (443 if https else 80)),
        "script_name": (parts.path.rstrip("/") + path),
        "get_data": dict(get or {}),
        "post_data": dict(post or {}),
    }


def metadata_xml(connection: SsoConnection, base: str) -> str:
    settings = OneLogin_Saml2_Settings(_settings(connection, base), sp_validation_only=True)
    metadata = settings.get_sp_metadata()
    errors = settings.validate_metadata(metadata)
    if errors:
        raise SamlError(f"Metadata invalid: {errors}")
    return metadata.decode() if isinstance(metadata, bytes) else metadata


def _check_ready(connection: SsoConnection) -> None:
    if not (connection.saml_sso_url and connection.saml_idp_certificate and connection.saml_sp_certificate):
        raise SamlError("The connection needs the identity provider's sign-in URL and certificate.")


def login_redirect(connection: SsoConnection, base: str, relay_state: str) -> tuple[str, str]:
    """(URL to send the browser to, the AuthnRequest's ID). Built by hand rather than with
    auth.login() so the request ID is known before the RelayState is."""
    _check_ready(connection)
    settings = _settings(connection, base)
    auth = OneLogin_Saml2_Auth(_request_data(base, "/"), old_settings=settings)
    request = OneLogin_Saml2_Authn_Request(auth.get_settings())
    parameters = {"SAMLRequest": request.get_request(), "RelayState": relay_state}
    auth.add_request_signature(parameters, _SIG)
    return auth.redirect_to(connection.saml_sso_url, parameters), request.get_id()


def process_response(connection: SsoConnection, base: str, post_data: dict, *, request_id: str | None) -> SamlAssertion:
    """Checks the identity provider's answer. `request_id` is the AuthnRequest it must answer,
    or None for sign-in started at the provider (only if the connection allows it)."""
    _check_ready(connection)
    if request_id is None and not connection.allow_idp_initiated:
        raise SamlError("Sign-in started at the identity provider isn't allowed for this connection.")
    urls = sp_urls(base, connection)
    auth = OneLogin_Saml2_Auth(
        _request_data(base, urlsplit(urls["acs"]).path.removeprefix(urlsplit(base).path.rstrip("/")), post=post_data),
        old_settings=_settings(connection, base),
    )
    try:
        auth.process_response(request_id=request_id)
    except Exception as exc:  # noqa: BLE001 -- malformed XML and the like
        raise SamlError(f"Unreadable SAML response: {exc}") from None
    errors = auth.get_errors()
    if errors or not auth.is_authenticated():
        raise SamlError(f"SAML response rejected: {errors} {auth.get_last_error_reason() or ''}".strip())
    in_response_to = auth.get_last_response_in_response_to()
    if request_id is not None and in_response_to != request_id:
        # python3-saml only compares InResponseTo when it's present; an unsolicited answer
        # mustn't complete a sign-in we started.
        raise SamlError("The SAML response doesn't answer the request this sign-in sent.")
    if request_id is None and in_response_to:
        # Unsolicited answers only; one carrying InResponseTo answers a request this browser didn't make.
        raise SamlError("The SAML response answers a request this sign-in didn't make.")

    attributes = auth.get_attributes() or {}
    name_id = auth.get_nameid() or ""
    # A transient NameID changes at every sign-in, so it can't identify the person: it isn't
    # recorded as the subject (services/sso_service.py matches people by subject).
    subject = "" if (auth.get_nameid_format() or "").endswith(":transient") else name_id
    email_values = attributes.get(connection.saml_email_attribute, []) if connection.saml_email_attribute else [name_id]
    email = (email_values[0] if email_values else "").strip().lower()
    if "@" not in email:
        raise SamlError("The SAML assertion has no email address.")
    expires = auth.get_last_assertion_not_on_or_after()
    return SamlAssertion(
        email=email, subject=subject, groups=[str(g) for g in attributes.get(connection.groups_claim_name, [])],
        assertion_id=auth.get_last_assertion_id() or "",
        expires_at=dt.datetime.fromtimestamp(expires, dt.timezone.utc) if expires else None,
        session_index=auth.get_session_index(), in_response_to=in_response_to,
        authentication=tuple(str(c) for c in (auth.get_last_authn_contexts() or [])),
        customer_number=(str(attributes[connection.customer_claim][0]).strip() or None)
        if connection.customer_claim and attributes.get(connection.customer_claim) else None,
    )


def process_logout_request(connection: SsoConnection, base: str, get_data: dict) -> tuple[str, str | None]:
    """Handles a LogoutRequest from the identity provider (HTTP-Redirect). It must be signed.
    Returns (the NameID to sign out, the URL to send the browser back to with our
    LogoutResponse)."""
    if "SAMLRequest" not in get_data or "Signature" not in get_data:
        raise SamlError("Only signed logout requests are accepted.")
    urls = sp_urls(base, connection)
    auth = OneLogin_Saml2_Auth(
        _request_data(base, urlsplit(urls["slo"]).path.removeprefix(urlsplit(base).path.rstrip("/")), get=get_data),
        old_settings=_settings(connection, base),
    )
    from onelogin.saml2.logout_request import OneLogin_Saml2_Logout_Request
    from onelogin.saml2.utils import OneLogin_Saml2_Utils

    request_xml = OneLogin_Saml2_Utils.decode_base64_and_inflate(get_data["SAMLRequest"])
    name_id = OneLogin_Saml2_Logout_Request.get_nameid(request_xml)
    try:
        redirect = auth.process_slo(keep_local_session=True)
    except Exception as exc:  # noqa: BLE001
        raise SamlError(f"Unreadable logout request: {exc}") from None
    if auth.get_errors():
        raise SamlError(f"Logout request rejected: {auth.get_errors()} {auth.get_last_error_reason() or ''}".strip())
    return name_id, redirect
