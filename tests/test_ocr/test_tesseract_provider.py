# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from decimal import Decimal

from tests.test_ocr.conftest import make_check_image


def test_extracts_amount_and_payee_from_clean_image():
    from openclear.ocr.tesseract_provider import TesseractOCRProvider

    image_bytes = make_check_image(payee="Beta Vendor Inc", amount="500.00")
    result = TesseractOCRProvider().extract(image_bytes)

    assert result.extracted_amount == Decimal("500.00")
    assert result.extracted_payee == "Beta Vendor Inc"
    assert result.confidence > 0.5


def test_provider_satisfies_ocr_result_confidence_bounds(sample_check_image_bytes):
    from openclear.ocr.tesseract_provider import TesseractOCRProvider

    result = TesseractOCRProvider().extract(sample_check_image_bytes)

    assert 0.0 <= result.confidence <= 1.0


def test_amount_extraction_picks_largest_dollar_figure_as_courtesy_amount():
    from openclear.ocr.tesseract_provider import TesseractOCRProvider

    text = "Check no. 12 dated 2026, amount due $1,250.00 total"
    amount = TesseractOCRProvider._extract_amount(text)

    assert amount == Decimal("1250.00")


def test_amount_extraction_returns_none_when_no_dollar_figure_present():
    from openclear.ocr.tesseract_provider import TesseractOCRProvider

    assert TesseractOCRProvider._extract_amount("no numbers here at all") is None


def test_payee_extraction_returns_none_without_label():
    from openclear.ocr.tesseract_provider import TesseractOCRProvider

    assert TesseractOCRProvider._extract_payee("just some random check text") is None


def test_ocr_provider_protocol_compliance():
    """Every registered OCR provider must expose .name and .extract() with the same
    shape — this is what lets factory.get_ocr_provider() swap providers transparently."""
    from openclear.ocr.tesseract_provider import TesseractOCRProvider

    provider = TesseractOCRProvider()
    assert isinstance(provider.name, str)
    assert callable(provider.extract)


def test_extract_passes_configured_timeout_to_both_pytesseract_calls(monkeypatch):
    """A pathological image could otherwise hang the tesseract subprocess indefinitely —
    confirms both calls actually receive the configured timeout, not just that OCR
    still works (already covered by the other tests here)."""
    import openclear.ocr.tesseract_provider as provider_module
    from openclear.config import get_settings

    monkeypatch.setattr(get_settings(), "ocr_timeout_seconds", 5)
    calls = []

    def fake_image_to_string(image, timeout=0, **kwargs):
        calls.append(("image_to_string", timeout))
        return "fake text"

    def fake_image_to_data(image, output_type=None, timeout=0, **kwargs):
        calls.append(("image_to_data", timeout))
        return {"conf": [], "text": []}

    monkeypatch.setattr(provider_module.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(provider_module.pytesseract, "image_to_data", fake_image_to_data)

    image_bytes = make_check_image(payee="Test Vendor", amount="10.00")
    provider_module.TesseractOCRProvider().extract(image_bytes)

    assert calls == [("image_to_string", 5), ("image_to_data", 5)]
