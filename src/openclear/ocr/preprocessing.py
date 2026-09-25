# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import io

from PIL import Image, ImageEnhance, ImageOps

# Tesseract accuracy on check images is highly sensitive to preprocessing: checks have
# background security patterns and a printed MICR line that read as noise without this.
_MIN_DIMENSION_PX = 1000  # upscale small scans; Tesseract does poorly below ~300dpi-equivalent


def preprocess_for_ocr(image_bytes: bytes) -> bytes:
    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)  # normalize phone-camera orientation
    image = image.convert("L")  # grayscale — drops color security-pattern noise

    if max(image.size) < _MIN_DIMENSION_PX:
        scale = _MIN_DIMENSION_PX / max(image.size)
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)

    image = ImageEnhance.Contrast(image).enhance(1.5)
    image = image.point(lambda px: 255 if px > 160 else 0)  # binarize

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
