# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import io

import pytest
from PIL import Image, ImageDraw, ImageFont


def make_check_image(*, payee: str = "Acme Test Vendor", amount: str = "123.45") -> bytes:
    img = Image.new("RGB", (1200, 500), "white")
    draw = ImageDraw.Draw(img)
    # Pillow's bundled scalable font, so the image is identical on every OS. A macOS system
    # font path used to fall back to Pillow's tiny bitmap font on Linux, which Tesseract
    # misreads (the CI failures fixed in SCALE_PLAN.md S0).
    font = ImageFont.load_default(size=36)

    draw.text((50, 50), "DATE: 01/10/2026", fill="black", font=font)
    draw.text((50, 150), f"PAY TO THE ORDER OF: {payee}", fill="black", font=font)
    draw.text((900, 150), f"${amount}", fill="black", font=font)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def sample_check_image_bytes() -> bytes:
    return make_check_image()
