# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel


class OCRResult(BaseModel):
    extracted_amount: Decimal | None
    extracted_payee: str | None
    confidence: float  # 0.0-1.0, normalized across providers
    raw_response: dict[str, Any]


class OCRProvider(Protocol):
    """Calling code (networks/check/ocr_processing.py) depends only on this protocol via
    factory.get_ocr_provider() — never on a concrete provider — so swapping Tesseract for
    a cloud vendor is a config change, not a code change."""

    name: str

    def extract(self, image_bytes: bytes, *, image_format: str = "png") -> OCRResult: ...


# Provider names (config.py's `ocr_provider` setting) whose .extract() is a bare
# `raise NotImplementedError` -- see textract_provider.py / azure_di_provider.py's own
# docstrings for what a real implementation would need. Kept here rather than in
# factory.py so config.py::assert_production_safe can check it without importing
# factory.py (which imports config.py itself -- that'd be a cycle) or constructing a
# provider (which would require its optional extra just to answer "is this a stub").
UNIMPLEMENTED_PROVIDERS: frozenset[str] = frozenset({"textract", "azure_document_intelligence"})
