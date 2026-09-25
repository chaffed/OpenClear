# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import io
import re
from decimal import Decimal, InvalidOperation

import pytesseract
from PIL import Image
from pytesseract import Output

from openclear.config import get_settings
from openclear.ocr.base import OCRResult
from openclear.ocr.preprocessing import preprocess_for_ocr

_AMOUNT_PATTERN = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})*\.\d{2})")
_PAYEE_LABEL_PATTERN = re.compile(r"(?:PAY\s+TO\s+THE\s+ORDER\s+OF|ORDER\s+OF)\s*[:\-]?\s*(.+)", re.IGNORECASE)


class TesseractOCRProvider:
    """Local, zero-cloud-dependency default. Tesseract is a general-purpose OCR engine
    with no check-specific training, so extraction here leans on regex/heuristic
    postprocessing (find the '$' amount, find text after 'PAY TO THE ORDER OF') rather
    than structured field extraction — this is genuinely a trial/low-volume-grade default,
    not a production accuracy story for handwritten checks. See ocr/base.py and the
    docs/ARCHITECTURE.md ("OCR") for why the provider is swappable."""

    name = "tesseract"

    def extract(self, image_bytes: bytes, *, image_format: str = "png") -> OCRResult:
        processed_bytes = preprocess_for_ocr(image_bytes)
        image = Image.open(io.BytesIO(processed_bytes))

        # A pathological/adversarial image can otherwise make the tesseract subprocess
        # hang indefinitely — pytesseract raises a plain RuntimeError on timeout, already
        # caught by networks/check/ocr_processing.py's existing broad exception handler.
        timeout = get_settings().ocr_timeout_seconds
        raw_text = pytesseract.image_to_string(image, timeout=timeout)
        word_data = pytesseract.image_to_data(image, output_type=Output.DICT, timeout=timeout)

        confidences = [float(c) for c in word_data.get("conf", []) if c not in ("-1", -1) and float(c) >= 0]
        confidence = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0

        return OCRResult(
            extracted_amount=self._extract_amount(raw_text),
            extracted_payee=self._extract_payee(raw_text),
            confidence=confidence,
            raw_response={"text": raw_text, "word_count": len(word_data.get("text", []))},
        )

    @staticmethod
    def _extract_amount(text: str) -> Decimal | None:
        matches = _AMOUNT_PATTERN.findall(text)
        if not matches:
            return None
        # The courtesy-amount box is usually the largest dollar figure on the check;
        # smaller matches are more likely to be stray fragments (check number, date-ish noise).
        candidates = []
        for m in matches:
            try:
                candidates.append(Decimal(m.replace(",", "")))
            except InvalidOperation:
                continue
        return max(candidates) if candidates else None

    @staticmethod
    def _extract_payee(text: str) -> str | None:
        for line in text.splitlines():
            match = _PAYEE_LABEL_PATTERN.search(line)
            if match:
                payee = match.group(1)
                # The amount box sometimes lands on the same OCR line as the payee text
                # (layout-dependent); trim anything from a '$' onward so it doesn't bleed in.
                payee = payee.split("$")[0]
                payee = payee.strip(" .:-\t")
                if payee:
                    return payee
        return None
