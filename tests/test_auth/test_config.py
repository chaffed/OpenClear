# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.config import Settings, assert_production_safe

@pytest.fixture(autouse=True)
def _a_default_cutoff(monkeypatch):
    """Production needs a default decision cut-off (config.assert_production_safe); the suite
    runs without one (tests/conftest.py), so these production configurations get one back."""
    monkeypatch.setenv("OPENCLEAR_DEFAULT_DECISION_CUTOFF", "11:00")


_OVERRIDDEN = {
    "jwt_private_key_path": "keys/jwt_private.pem",
    "jwt_public_key_path": "keys/jwt_public.pem",
    "file_signing_private_key_path": "keys/file_signing_private.pem",
    "file_signing_public_key_path": "keys/file_signing_public.pem",
    "audit_log_signing_private_key_path": "keys/audit_log_signing_private.pem",
    "audit_log_signing_public_key_path": "keys/audit_log_signing_public.pem",
    "sso_encryption_key": "a-real-random-secret-not-the-checked-in-default",
}


def test_development_environment_never_raises_even_with_all_defaults():
    settings = Settings(environment="development")
    assert_production_safe(settings)  # must not raise


def test_production_environment_raises_with_all_defaults():
    settings = Settings(environment="production")
    with pytest.raises(RuntimeError, match="insecure checked-in dev/test defaults"):
        assert_production_safe(settings)


def test_production_environment_passes_once_every_default_is_overridden():
    settings = Settings(environment="production", **_OVERRIDDEN)
    assert_production_safe(settings)  # must not raise


@pytest.mark.parametrize("field_left_default", list(_OVERRIDDEN))
def test_production_environment_raises_if_any_single_field_still_default(field_left_default):
    overrides = {k: v for k, v in _OVERRIDDEN.items() if k != field_left_default}
    settings = Settings(environment="production", **overrides)
    with pytest.raises(RuntimeError, match=field_left_default):
        assert_production_safe(settings)


@pytest.mark.parametrize("provider_name", ["textract", "azure_document_intelligence"])
def test_production_environment_raises_with_unimplemented_ocr_provider(provider_name):
    # A stub provider's .extract() only ever raises the moment someone uploads a check
    # image -- this must be caught at startup instead, not left to surface at first use.
    settings = Settings(environment="production", ocr_provider=provider_name, **_OVERRIDDEN)
    with pytest.raises(RuntimeError, match="has no working implementation"):
        assert_production_safe(settings)


def test_production_environment_passes_with_tesseract_ocr_provider():
    settings = Settings(environment="production", ocr_provider="tesseract", **_OVERRIDDEN)
    assert_production_safe(settings)  # must not raise


def test_production_environment_refuses_private_oidc_hosts():
    # Lets anyone who can edit an SSO connection aim server-side requests at internal
    # addresses (auth/outbound_http.py) — local testing only.
    settings = Settings(environment="production", oidc_allow_private_hosts=True, **_OVERRIDDEN)
    with pytest.raises(RuntimeError, match="OIDC_ALLOW_PRIVATE_HOSTS"):
        assert_production_safe(settings)


@pytest.mark.parametrize("settings_given", [
    {"azure_storage_account_url": "https://acct.blob.core.windows.net"},  # no container
    {"azure_storage_container": "files"},  # nothing to sign in with
])
def test_production_environment_refuses_azure_storage_half_configured(settings_given):
    settings = Settings(environment="production", storage_backend="azure", **settings_given, **_OVERRIDDEN)
    with pytest.raises(RuntimeError, match="AZURE_STORAGE_CONTAINER"):
        assert_production_safe(settings)


def test_production_environment_passes_with_azure_storage_configured():
    settings = Settings(environment="production", storage_backend="azure", azure_storage_container="files",
                        azure_storage_account_url="https://acct.blob.core.windows.net", **_OVERRIDDEN)
    assert_production_safe(settings)  # must not raise


@pytest.mark.asvs("12.3.2")
def test_production_refuses_unverified_siem_tls():
    with pytest.raises(RuntimeError, match="SIEM_TLS_VERIFY"):
        assert_production_safe(Settings(environment="production", siem_tls_verify=False, **_OVERRIDDEN))


@pytest.mark.asvs("12.1.1")
def test_siem_tls_needs_1_2_or_newer():
    import ssl

    from openclear.services.siem.senders import Target, _tls_context

    target = Target(kind="syslog", host="siem.example.com", port=6514, transport="tls")
    assert _tls_context(target).minimum_version == ssl.TLSVersion.TLSv1_2


@pytest.mark.asvs("11.2.3")
def test_new_saml_connections_get_rsa_3072_keys():
    import uuid

    from cryptography import x509

    from openclear.auth.saml_service import generate_sp_keypair

    certificate_pem, _encrypted_key = generate_sp_keypair(uuid.uuid4())
    assert x509.load_pem_x509_certificate(certificate_pem.encode()).public_key().key_size == 3072
