# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Normalizes an incoming check image (from a ZIP manifest entry or an X9.37 Image View
Data record) to PNG bytes before storage, regardless of source format — everything
downstream (Tesseract OCR, the check-image download routes) then only ever has to deal
with one guaranteed-decodable format. A multi-page TIFF (the common way a single scan
captures both sides of a check) is split into its two frames; page 1 is the front, page 2
the back, unless the caller already has a separate back image, which always wins.
"""

import io

from PIL import Image, UnidentifiedImageError

_SUPPORTED_FORMATS = {"TIFF", "JPEG", "PNG"}


class UnsupportedImageError(ValueError):
    """Raised when a referenced/embedded image isn't a TIFF, JPEG, or PNG, or can't be
    decoded at all — the message is written to be read on a bulk-upload results row, not
    by a developer."""


def _to_png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    # Pillow can't write "P"/"1" (palette/bilevel, common for Group 4 TIFF) or "CMYK"
    # frames straight to PNG in every case — converting to RGB first is cheap and always
    # safe for a re-encode whose only purpose is guaranteed downstream decodability.
    if image.mode not in ("RGB", "RGBA", "L"):
        image = image.convert("RGB")
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def normalize_and_split_image(data: bytes, filename: str, *, back_data: bytes | None = None) -> tuple[bytes, bytes | None]:
    """Decodes `data` (the front image, or a combined front+back scan), validates it's a
    TIFF/JPEG/PNG, and returns `(front_png_bytes, back_png_bytes_or_None)`. If `back_data`
    is given, it's decoded and normalized independently and always wins over any second
    page found in `data`. Otherwise, a multi-page TIFF in `data` is split: page 1 -> front,
    page 2 -> back. Raises UnsupportedImageError for anything that isn't a valid
    TIFF/JPEG/PNG, or a multi-page TIFF with more than 2 pages (ambiguous — which pages
    are front/back isn't well-defined past a duplex scan)."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except UnidentifiedImageError:
        raise UnsupportedImageError(f"{filename!r} is not a readable TIFF, JPEG, or PNG image") from None
    except Image.DecompressionBombError as exc:
        # Pillow's own default decompression-bomb guard (Image.MAX_IMAGE_PIXELS, never
        # overridden anywhere in this app) already blocks a small-on-disk, huge-when-
        # decoded image — this just gives that the same clean, per-row error shape as
        # every other rejection here, instead of a raw Pillow exception.
        raise UnsupportedImageError(f"{filename!r} is too large to process safely: {exc}") from None
    except OSError as exc:
        raise UnsupportedImageError(f"{filename!r} could not be decoded: {exc}") from None

    if image.format not in _SUPPORTED_FORMATS:
        raise UnsupportedImageError(f"{filename!r} is a {image.format or 'unknown'} image — only TIFF, JPEG, and PNG are supported")

    n_frames = getattr(image, "n_frames", 1)
    if n_frames > 2:
        raise UnsupportedImageError(
            f"{filename!r} is a {n_frames}-page TIFF — only single-page images or a 2-page (front+back) TIFF are supported"
        )

    image.seek(0)
    front_png = _to_png_bytes(image.copy())

    back_png: bytes | None = None
    if back_data is not None:
        try:
            back_image = Image.open(io.BytesIO(back_data))
            back_image.load()
        except UnidentifiedImageError:
            raise UnsupportedImageError("back image is not a readable TIFF, JPEG, or PNG image") from None
        except Image.DecompressionBombError as exc:
            raise UnsupportedImageError(f"back image is too large to process safely: {exc}") from None
        except OSError as exc:
            raise UnsupportedImageError(f"back image could not be decoded: {exc}") from None
        if back_image.format not in _SUPPORTED_FORMATS:
            raise UnsupportedImageError(f"back image is a {back_image.format or 'unknown'} image — only TIFF, JPEG, and PNG are supported")
        back_png = _to_png_bytes(back_image)
    elif n_frames == 2:
        image.seek(1)
        back_png = _to_png_bytes(image.copy())

    return front_png, back_png
