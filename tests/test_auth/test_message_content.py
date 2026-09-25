# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import base64
import io

import pytest
from PIL import Image

from openclear.services.message_content import InvalidMessageContent, validate_and_normalize_message


def _png_data_uri(size=(10, 10), color="red"):
    image = Image.new("RGB", size, color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}"


def _noisy_png_data_uri_over(min_bytes):
    # A solid-color PNG compresses far too well to reliably exceed a byte cap -- random
    # noise doesn't compress, so this reliably produces something large regardless of
    # Pillow's PNG compression level.
    import random

    rng = random.Random(42)
    side = 400
    image = Image.new("RGB", (side, side))
    image.putdata([(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)) for _ in range(side * side)])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    assert len(data) > min_bytes, "test fixture didn't produce a large enough image"
    return f"data:image/png;base64,{base64.b64encode(data).decode()}"


def test_none_and_blank_normalize_to_none():
    assert validate_and_normalize_message(None) is None
    assert validate_and_normalize_message("") is None
    assert validate_and_normalize_message("   ") is None


def test_plain_text_passes_through_stripped():
    assert validate_and_normalize_message("  hello world  ") == "hello world"


def test_valid_embedded_image_is_preserved_and_reencoded():
    uri = _png_data_uri()
    result = validate_and_normalize_message(f"Notice: ![logo]({uri})")
    assert "data:image/png;base64," in result
    # Round-trip through Pillow -- confirm what comes out is still a valid, same-size PNG.
    encoded = result.split("base64,", 1)[1]
    reopened = Image.open(io.BytesIO(base64.b64decode(encoded)))
    reopened.load()
    assert reopened.format == "PNG"
    assert reopened.size == (10, 10)


def test_rejects_oversized_image():
    uri = _noisy_png_data_uri_over(150 * 1024)
    with pytest.raises(InvalidMessageContent, match="150 KB or smaller"):
        validate_and_normalize_message(f"![x]({uri})")


def test_rejects_disallowed_mime_type():
    svg_b64 = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>").decode()
    with pytest.raises(InvalidMessageContent, match="Unsupported embedded image type"):
        validate_and_normalize_message(f"![x](data:image/svg+xml;base64,{svg_b64})")


def test_rejects_corrupt_base64():
    with pytest.raises(InvalidMessageContent, match="corrupt"):
        validate_and_normalize_message("![x](data:image/png;base64,not-valid-base64-!!!)")


def test_rejects_mismatched_declared_format():
    png_b64 = _png_data_uri().split("base64,", 1)[1]
    with pytest.raises(InvalidMessageContent, match="labeled 'image/jpeg' but is actually 'PNG'"):
        validate_and_normalize_message(f"![x](data:image/jpeg;base64,{png_b64})")


def test_rejects_overlong_message():
    with pytest.raises(InvalidMessageContent, match="KB or smaller"):
        validate_and_normalize_message("x" * 500_001)


def test_multiple_images_in_one_message_all_validated():
    uri1 = _png_data_uri(color="red")
    uri2 = _png_data_uri(color="blue")
    result = validate_and_normalize_message(f"![a]({uri1}) and ![b]({uri2})")
    assert result.count("data:image/png;base64,") == 2
