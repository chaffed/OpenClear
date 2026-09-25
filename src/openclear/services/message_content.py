# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Validates and normalizes a tenant/customer Markdown message
(services/tenant_service.py::set_messages, services/customer_service.py::
set_banner_message) before it's stored — specifically, any embedded
`data:image/...;base64,...` image (inserted by the toolbar in
templates/_macros/markdown_editor.html's "Insert image" button, or hand-typed).

This is a save-time counterpart to web/templates.py::render_markdown's render-time
sanitization, not a replacement for it — see that module's own comment. Two independent
layers: this module guarantees only a genuinely well-formed, size-bounded raster image of
an allowed type ever reaches the database in the first place (even from a request that
bypasses the client-side toolbar entirely); render_markdown's nh3 pass then guarantees
that whatever text a template renders is safe regardless of how it got into the database.

Deliberately excludes SVG (`image/svg+xml`) despite Pillow being able to decode raster
previews of some SVGs in newer builds -- an SVG can embed a <script> tag, confirmed by
direct testing that nh3's own scheme-based allowlisting alone doesn't catch this (see
this feature's plan)."""

import base64
import binascii
import io
import re

from PIL import Image, UnidentifiedImageError

# No SVG or ICO here (unlike tenant_service.py's own _ALLOWED_IMAGE_CONTENT_TYPES, used
# for logo/favicon uploads) -- SVG can carry a <script> tag, and ICO is a multi-image
# container not worth supporting for a one-image inline banner graphic.
_ALLOWED_IMAGE_MIME_TYPES: dict[str, str] = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
}
_MAX_IMAGE_BYTES = 150 * 1024  # per embedded image, pre-base64 -- a small inline graphic, not a photo
_MAX_MESSAGE_LENGTH = 500_000  # total message text, including any embedded images, post-base64

_DATA_URI_RE = re.compile(r"data:image/([a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/]+=*)")


class InvalidMessageContent(ValueError):
    """Raised for a message that's too long, or an embedded image that's too large,
    the wrong format, or not a genuinely decodable image -- callers turn this into a
    422 form error, same as every other InvalidTenantSettingsInput-style validation in
    this app."""


def _reencode_image(mime_type: str, decoded: bytes) -> bytes:
    pillow_format = _ALLOWED_IMAGE_MIME_TYPES[mime_type]
    try:
        image = Image.open(io.BytesIO(decoded))
        image.load()
    except UnidentifiedImageError:
        raise InvalidMessageContent("One of the embedded images isn't a readable image file") from None
    except Image.DecompressionBombError as exc:
        # Pillow's own default decompression-bomb guard (Image.MAX_IMAGE_PIXELS, never
        # overridden anywhere in this app) -- same handling as bulk_import/images.py.
        raise InvalidMessageContent(f"One of the embedded images is too large to process safely: {exc}") from None
    except OSError as exc:
        raise InvalidMessageContent(f"One of the embedded images could not be decoded: {exc}") from None

    if image.format != pillow_format:
        raise InvalidMessageContent(
            f"An embedded image was labeled {mime_type!r} but is actually {image.format or 'unknown'!r}"
        )

    # JPEG has no alpha channel -- flatten onto white rather than letting Pillow raise.
    if pillow_format == "JPEG" and image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format=pillow_format)
    return buffer.getvalue()


def _replace_data_uri(match: re.Match) -> str:
    subtype, encoded = match.group(1), match.group(2)
    mime_type = f"image/{subtype.lower()}"
    if mime_type not in _ALLOWED_IMAGE_MIME_TYPES:
        raise InvalidMessageContent(
            f"Unsupported embedded image type {mime_type!r} -- use PNG, JPEG, GIF, or WEBP"
        )

    try:
        decoded = base64.b64decode(encoded, validate=True)
    except binascii.Error:
        raise InvalidMessageContent("One of the embedded images has corrupt image data") from None

    if len(decoded) > _MAX_IMAGE_BYTES:
        raise InvalidMessageContent(f"Each embedded image must be {_MAX_IMAGE_BYTES // 1024} KB or smaller")

    reencoded = _reencode_image(mime_type, decoded)
    return f"data:{mime_type};base64,{base64.b64encode(reencoded).decode('ascii')}"


def validate_and_normalize_message(text: str | None) -> str | None:
    """Blank/None input normalizes to None. Every embedded data:image/...;base64,...
    found in the text is validated and re-encoded through Pillow (guaranteeing the bytes
    ultimately stored are exactly what Pillow itself produced, not just
    "parses as an image" attacker-supplied bytes -- also strips any metadata/trailing-data
    tricks). Raises InvalidMessageContent for an oversized/invalid image, or a message
    that's too long overall."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None

    normalized = _DATA_URI_RE.sub(_replace_data_uri, text)

    if len(normalized) > _MAX_MESSAGE_LENGTH:
        raise InvalidMessageContent(f"Message must be {_MAX_MESSAGE_LENGTH // 1000} KB or smaller in total")

    return normalized
