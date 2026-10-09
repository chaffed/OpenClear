# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import logging

from openclear.config import Settings, get_settings
from openclear.ocr.base import UNIMPLEMENTED_PROVIDERS, OCRProvider

logger = logging.getLogger(__name__)

_PROVIDER_CACHE: dict[str, OCRProvider] = {}


def get_ocr_provider(settings: Settings | None = None) -> OCRProvider:
    settings = settings or get_settings()
    provider_name = settings.ocr_provider

    if provider_name in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[provider_name]

    if provider_name in UNIMPLEMENTED_PROVIDERS:
        # config.py::assert_production_safe already refuses to start at all with this
        # configured in production; this is the dev/test-time equivalent — surfaced here
        # rather than silently constructing a provider whose .extract() is a stub, so
        # this doesn't only show up the moment someone actually uploads a check image.
        logger.warning(
            "ocr_provider=%r has no working implementation yet — .extract() will raise "
            "NotImplementedError. Only 'tesseract' is currently implemented.",
            provider_name,
        )

    from openclear import plugins

    provider: OCRProvider = plugins.lookup("ocr_providers", provider_name, what="OCR provider")()

    _PROVIDER_CACHE[provider_name] = provider
    return provider
