# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from openclear.ocr.base import OCRResult


class TextractOCRProvider:
    """AWS Textract implements the same OCRProvider protocol as TesseractOCRProvider.
    Textract's AnalyzeExpense API does real structured field extraction (not regex over
    raw text), which is a qualitative accuracy jump over Tesseract, not just an incremental
    one — worth it once check volume justifies the cloud dependency and cost.

    Stub: requires `pip install openclear[textract]` (boto3) plus AWS credentials configured
    via the standard boto3 credential chain. Not wired up to real AnalyzeExpense calls in
    this build — implementing and testing that requires a live AWS account."""

    name = "textract"

    def __init__(self) -> None:
        try:
            import boto3  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "textract OCR provider requires the 'textract' extra: pip install openclear[textract]"
            ) from exc

    def extract(self, image_bytes: bytes, *, image_format: str = "png") -> OCRResult:
        raise NotImplementedError(
            "TextractOCRProvider.extract() is a stub — wire up boto3's textract "
            "AnalyzeExpense call here when a live AWS account is available to test against."
        )
