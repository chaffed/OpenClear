# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import logging

import pytest

from openclear.config import Settings
from openclear.ocr.base import UNIMPLEMENTED_PROVIDERS
from openclear.ocr.factory import _PROVIDER_CACHE, get_ocr_provider


def test_unimplemented_providers_are_exactly_textract_and_azure_di():
    assert UNIMPLEMENTED_PROVIDERS == frozenset({"textract", "azure_document_intelligence"})


def test_get_ocr_provider_does_not_warn_for_tesseract(caplog):
    _PROVIDER_CACHE.clear()
    settings = Settings(environment="development", ocr_provider="tesseract")
    with caplog.at_level(logging.WARNING):
        get_ocr_provider(settings)
    assert not any("no working implementation" in r.message for r in caplog.records)


@pytest.mark.parametrize("provider_name", sorted(UNIMPLEMENTED_PROVIDERS))
def test_get_ocr_provider_warns_before_constructing_unimplemented_provider(caplog, provider_name):
    # Should log the warning regardless of whether the provider's own optional extra
    # (boto3 / azure-ai-documentintelligence) happens to be installed in this
    # environment -- the warning fires before construction is even attempted, and a
    # missing-extra RuntimeError here is expected/fine in an environment that hasn't
    # installed that provider's extra.
    _PROVIDER_CACHE.clear()
    settings = Settings(environment="development", ocr_provider=provider_name)
    with caplog.at_level(logging.WARNING):
        try:
            get_ocr_provider(settings)
        except RuntimeError:
            pass
    assert any("no working implementation" in r.message for r in caplog.records)
