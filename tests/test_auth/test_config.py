# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import pytest

from openclear.config import Settings, assert_production_safe

_OVERRIDDEN = {
    "jwt_private_key_path": "keys/jwt_private.pem",
    "jwt_public_key_path": "keys/jwt_public.pem",
    "file_signing_private_key_path": "keys/file_signing_private.pem",
    "file_signing_public_key_path": "keys/file_signing_public.pem",
    "audit_log_signing_private_key_path": "keys/audit_log_signing_private.pem",
    "audit_log_signing_public_key_path": "keys/audit_log_signing_public.pem",
    "wsud_signing_private_key_path": "keys/wsud_signing_private.pem",
    "wsud_signing_public_key_path": "keys/wsud_signing_public.pem",
    "sso_encryption_key": "a-real-random-secret-not-the-checked-in-default",
    "wsud_consent_disclosure_text": "Real, counsel-reviewed consent disclosure text.",
    "wsud_attestation_text": "Real, counsel-reviewed attestation text.",
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
