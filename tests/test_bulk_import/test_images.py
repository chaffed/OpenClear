# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import io

import pytest
from PIL import Image

from openclear.bulk_import.images import UnsupportedImageError, normalize_and_split_image


def _encode(fmt: str, *, size=(20, 10), color="white") -> bytes:
    img = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    img.save(buffer, format=fmt)
    return buffer.getvalue()


def _encode_multipage_tiff(colors: list[str]) -> bytes:
    images = [Image.new("RGB", (20, 10), c) for c in colors]
    buffer = io.BytesIO()
    images[0].save(buffer, format="TIFF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


def test_single_page_png_passes_through_with_no_back(tmp_path):
    data = _encode("PNG")
    front, back = normalize_and_split_image(data, "check.png")
    assert Image.open(io.BytesIO(front)).format == "PNG"
    assert back is None


def test_single_page_jpeg_normalizes_to_png():
    data = _encode("JPEG")
    front, back = normalize_and_split_image(data, "check.jpg")
    assert Image.open(io.BytesIO(front)).format == "PNG"
    assert back is None


def test_single_page_tiff_normalizes_to_png():
    data = _encode("TIFF")
    front, back = normalize_and_split_image(data, "check.tif")
    assert Image.open(io.BytesIO(front)).format == "PNG"
    assert back is None


def test_two_page_tiff_splits_into_front_and_back():
    data = _encode_multipage_tiff(["white", "black"])
    front, back = normalize_and_split_image(data, "check.tif")
    assert back is not None
    front_img = Image.open(io.BytesIO(front))
    back_img = Image.open(io.BytesIO(back))
    assert front_img.getpixel((0, 0)) == (255, 255, 255)
    assert back_img.getpixel((0, 0)) == (0, 0, 0)


def test_three_page_tiff_is_rejected():
    data = _encode_multipage_tiff(["white", "black", "red"])
    with pytest.raises(UnsupportedImageError):
        normalize_and_split_image(data, "check.tif")


def test_explicit_back_image_overrides_multipage_tiff_second_page():
    front_data = _encode_multipage_tiff(["white", "black"])
    explicit_back = _encode("PNG", color="red")
    front, back = normalize_and_split_image(front_data, "check.tif", back_data=explicit_back)
    back_img = Image.open(io.BytesIO(back))
    assert back_img.getpixel((0, 0)) == (255, 0, 0)


def test_unreadable_bytes_are_rejected():
    with pytest.raises(UnsupportedImageError):
        normalize_and_split_image(b"not an image at all", "check.png")


def test_back_image_must_also_be_a_supported_format():
    front_data = _encode("PNG")
    with pytest.raises(UnsupportedImageError):
        normalize_and_split_image(front_data, "check.png", back_data=b"garbage")


def test_decompression_bomb_is_rejected_with_a_clean_message(monkeypatch):
    # Lower Pillow's own default guard so an otherwise-ordinary image trips it, proving
    # normalize_and_split_image converts Image.DecompressionBombError into the same
    # UnsupportedImageError shape as every other rejection, rather than a raw Pillow
    # exception escaping.
    data = _encode("PNG", size=(200, 200))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(UnsupportedImageError, match="too large to process safely"):
        normalize_and_split_image(data, "check.png")


def test_decompression_bomb_on_back_image_is_rejected_with_a_clean_message(monkeypatch):
    front_data = _encode("PNG", size=(5, 5))  # stays under the lowered threshold below
    back_data = _encode("PNG", size=(200, 200))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(UnsupportedImageError, match="back image is too large to process safely"):
        normalize_and_split_image(front_data, "check.png", back_data=back_data)
