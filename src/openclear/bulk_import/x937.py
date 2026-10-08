# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Parser for X9.37 ("DSTU X9.37" / Check 21) image cash letter files.

**Framing.** Three are read, detected from the first bytes:
- the standard: each record preceded by a 4-byte big-endian record length (what banks,
  processors and the Federal Reserve exchange);
- records followed by CR and/or LF;
- records simply concatenated.

In the last two, every record except Image View Data (Type 52) is 80 bytes, and a Type
52's length is computed from its own length fields.

**Encoding.** EBCDIC (code page 037, the usual one for X9 files) or ASCII, detected from
the first record's type. Image data is binary either way and is never decoded.

**Field positions** follow DSTU X9.37-2003 and were checked byte for byte against Moov's
open source implementation and its sample files (moov-io/imagecashletter, Apache-2.0;
see tests/fixtures/moov/). Not real bank files, but the reference most US X9 tooling
agrees with.

**What's extracted.** OpenClear reads only what it needs to create a paid_item and a
check_image per check. From each Check Detail Record (Type 25) it takes:
- the Payor Bank Routing Number;
- the item amount;
- the On-Us field, split at the MICR on-us symbol (`/`): the account number is the part
  before it, without spaces or dashes;
- the check number, from the Auxiliary On-Us field (where business checks carry the
  serial), or failing that the part of On-Us after the `/` (where personal checks do).
  Banks lay out on-us data differently, so a file that doesn't follow this convention
  won't resolve; the ZIP+CSV upload is the explicit-column fallback.

Images come from the Image View Detail (Type 50) and Image View Data (Type 52) pairs after
each Check Detail Record: the first pair is the front, the second the back (the duplex
scan convention).

**Validation.** Control totals are checked at every level: Bundle Control (Type 70)
against its bundle, Cash Letter Control (Type 90) against the cash letter, and File
Control (Type 99) against the whole file, item count and total amount each. A mismatch,
an unknown record type, or a truncated record rejects the whole file.

**Not supported:** return and adjustment cash letters, and credit reconcilement records
beyond skipping the Credit record (Type 61).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class X937ParseError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class X937CheckItem:
    item_number: int  # 1-based position among check detail records in this file, for error messages
    routing_number: str
    account_number: str  # from the On-Us field, before the on-us symbol; may be blank
    check_number: str  # Auxiliary On-Us, or the On-Us part after the symbol; may be blank
    amount: Decimal
    presented_date: date  # from the enclosing Cash Letter Header's Business Date, or today if unparseable
    front_image_bytes: bytes  # empty if no Image View Data record followed this check's detail record
    back_image_bytes: bytes | None
    # Set instead of the bytes above when parsed with lazy_images=True: (start, end)
    # offsets of each image in the file, read later with item_images().
    front_image_span: tuple[int, int] | None = None
    back_image_span: tuple[int, int] | None = None
    on_us: str = ""  # the raw On-Us field, for reference


def item_images(item: X937CheckItem, content=None) -> tuple[bytes, bytes | None]:
    """The item's (front, back) image bytes. For an item parsed with lazy_images=True,
    `content` must be the same file (bytes or an mmap) it was parsed from."""
    if item.front_image_span is None:
        return item.front_image_bytes, item.back_image_bytes
    front = bytes(content[item.front_image_span[0] : item.front_image_span[1]])
    back = bytes(content[item.back_image_span[0] : item.back_image_span[1]]) if item.back_image_span else None
    return front, back


_STANDARD_RECORD_LEN = 80  # every record type except 52 (Image View Data)

# Type 10 - Cash Letter Header: Cash Letter Business Date (8, positions 23-30, YYYYMMDD).
_T10_BUSINESS_DATE = slice(22, 30)

# Type 25 - Check Detail.
_T25_AUX_ON_US = slice(2, 17)  # Auxiliary On-Us (15, positions 3-17)
_T25_ROUTING_NUMBER = slice(18, 26)  # Payor Bank Routing Number (8, positions 19-26)
_T25_ON_US = slice(27, 47)  # On-Us (20, positions 28-47)
_T25_AMOUNT = slice(47, 57)  # Item Amount (10, positions 48-57, implied 2 decimals)

# Type 52 - Image View Data: fixed fields up to the clipping coordinates (101 bytes),
# then three length-prefixed variable fields: Image Reference Key (4-digit length),
# Digital Signature (5-digit length), Image Data (7-digit length).
_T52_FIXED_LEN = 101
_T52_LENGTH_FIELDS = ((4, "image reference key"), (5, "digital signature"), (7, "image data"))

# Type 70 - Bundle Control: Items Within Bundle Count (4, positions 3-6), Bundle Total
# Amount (12, positions 7-18).
_T70_ITEMS_COUNT = slice(2, 6)
_T70_TOTAL_AMOUNT = slice(6, 18)

# Type 90 - Cash Letter Control: Items Within Cash Letter Count (8, positions 9-16), Cash
# Letter Total Amount (14, positions 17-30).
_T90_ITEMS_COUNT = slice(8, 16)
_T90_TOTAL_AMOUNT = slice(16, 30)

# Type 99 - File Control: Total Item Count (8, positions 17-24), File Total Amount (16,
# positions 25-40).
_T99_ITEMS_COUNT = slice(16, 24)
_T99_TOTAL_AMOUNT = slice(24, 40)

# Read past without using: file header, image view detail and analysis, the addendum
# records, credit, and the routing number summary.
_SKIP_RECORD_TYPES = {"01", "26", "27", "28", "50", "54", "61", "62", "68", "85"}
# Return records: a return cash letter, which isn't what this import is for.
_RETURN_RECORD_TYPES = {"31", "32", "33", "34", "35"}

_ON_US_SYMBOL = "/"


def _parse_business_date(raw: str) -> date:
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except (ValueError, IndexError):
        return date.today()


def _declared(text: str, field: slice, *, field_name: str, offset: int) -> int:
    raw = text[field].strip()
    try:
        return int(raw or "0")
    except ValueError:
        raise X937ParseError(f"Control record at byte {offset} has a non-numeric {field_name} field: {raw!r}") from None


def _check_totals(text: str, items_field: slice, amount_field: slice, *, items: int, cents: int, offset: int, scope: str) -> None:
    for field, field_name, actual in ((items_field, "items count", items), (amount_field, "total amount", cents)):
        declared = _declared(text, field, field_name=field_name, offset=offset)
        if declared != actual:
            raise X937ParseError(
                f"{scope} Control Record at byte {offset}: declared {field_name} {declared} doesn't match the {actual} "
                f"actually found in the {scope.lower()}"
            )


def split_on_us(on_us: str, aux_on_us: str) -> tuple[str, str]:
    """(account number, check number) from the MICR On-Us and Auxiliary On-Us fields."""
    account_part, _, serial_part = on_us.partition(_ON_US_SYMBOL)
    account = account_part.replace(" ", "").replace("-", "")
    check = aux_on_us.strip() or serial_part.replace(" ", "").replace("-", "")
    return account, check


# --- Reading records ---------------------------------------------------------------------


def _detect(content) -> tuple[bool, str]:
    """(length-prefixed?, text encoding) from the first bytes."""
    head = bytes(content[:6])
    if len(head) >= 6 and int.from_bytes(head[:4], "big") >= 2 and head[4:6] in (b"01", b"\xf0\xf1"):
        return True, "cp037" if head[4:6] == b"\xf0\xf1" else "ascii"
    if head[:2] in (b"01", b"\xf0\xf1"):
        return False, "cp037" if head[:2] == b"\xf0\xf1" else "ascii"
    raise X937ParseError("Not an X9.37 file: it doesn't start with a File Header Record (type 01)")


def _skip_separators(content, cursor: int) -> int:
    while cursor < len(content) and content[cursor] in (0x0D, 0x0A):
        cursor += 1
    return cursor


def _image_span(content, start: int, limit: int, encoding: str) -> tuple[int, int]:
    """For a Type 52 record starting at `start`: where its image data is. Raises
    X937ParseError if a length field isn't numeric or runs past `limit`."""
    cursor = start + _T52_FIXED_LEN
    for digits, name in _T52_LENGTH_FIELDS:
        if cursor + digits > limit:
            raise X937ParseError(f"Truncated Image View Data Record (Type 52) at byte {start}")
        raw = bytes(content[cursor : cursor + digits]).decode(encoding, errors="replace")
        try:
            length = int(raw)
        except ValueError:
            raise X937ParseError(
                f"Image View Data Record (Type 52) at byte {start} has a non-numeric {name} length: {raw!r}"
            ) from None
        cursor += digits
        if cursor + length > limit:
            raise X937ParseError(
                f"Image View Data Record (Type 52) at byte {start} declares a {name} length ({length}) "
                "that doesn't fit in the record"
            )
        if name == "image data":
            return cursor, cursor + length
        cursor += length
    raise AssertionError("unreachable")


def _records(content, prefixed: bool, encoding: str) -> Iterator[tuple[int, str, str, tuple[int, int] | None]]:
    """Yields (offset, record type, the record's text, image span or None) for each record.
    A Type 52's text is only its fixed part: its image is never decoded or copied."""
    size = len(content)
    cursor = 0
    while cursor < size:
        if prefixed:
            if cursor + 4 > size:
                raise X937ParseError(f"Truncated record length at byte {cursor}")
            length = int.from_bytes(bytes(content[cursor : cursor + 4]), "big")
            start, end = cursor + 4, cursor + 4 + length
            if length < 2 or end > size:
                raise X937ParseError(f"Record at byte {cursor} declares a length ({length}) that doesn't fit in the file")
        else:
            if size - cursor < 2:
                raise X937ParseError(f"Truncated record at byte {cursor} (not enough bytes for a record type)")
            start, end = cursor, None
        record_type = bytes(content[start : start + 2]).decode(encoding, errors="replace")
        if record_type == "52":
            span = _image_span(content, start, end if prefixed else size, encoding)
            text = bytes(content[start : start + _T52_FIXED_LEN]).decode(encoding, errors="replace")
            end = end if prefixed else span[1]
        else:
            if not prefixed:
                end = start + _STANDARD_RECORD_LEN
                if end > size:
                    raise X937ParseError(f"Truncated record type {record_type!r} at byte {cursor}")
            span, text = None, bytes(content[start:end]).decode(encoding, errors="replace")
        yield cursor, record_type, text, span
        cursor = end if prefixed else _skip_separators(content, end)


# --- The file ------------------------------------------------------------------------------


def parse_x937_file(content, *, lazy_images: bool = False) -> list[X937CheckItem]:
    """One X937CheckItem per Check Detail Record (Type 25), with its following image pair
    (if any) attached. Raises X937ParseError for anything that shows the file doesn't hold
    together: an unknown record type, a truncated record, an image length that doesn't fit,
    or a control total that doesn't match. These are whole-file problems, not a single bad
    check, since there's no reliable way to resynchronize past them.

    `content` is bytes or anything that slices like it, such as an mmap of the stored
    file. With lazy_images=True the image bytes aren't copied out: each item records where
    its images are, so a large cash letter isn't held in memory (SCALE_PLAN.md S4)."""
    if len(content) < 2:
        raise X937ParseError("File is too short to be an X9.37 file")
    prefixed, encoding = _detect(content)
    items: list[X937CheckItem] = []
    current: dict | None = None
    business_date = date.today()
    bundle = [0, 0]  # items, cents
    letter = [0, 0]
    whole = [0, 0]

    def flush() -> None:
        if current is None:
            return
        images = current.pop("images")
        if lazy_images:
            front_bytes, back_bytes = b"", None
            front_span, back_span = (images[0] if images else (0, 0)), (images[1] if len(images) >= 2 else None)
        else:
            front_bytes = bytes(content[images[0][0] : images[0][1]]) if images else b""
            back_bytes = bytes(content[images[1][0] : images[1][1]]) if len(images) >= 2 else None
            front_span = back_span = None
        items.append(X937CheckItem(
            **current, front_image_bytes=front_bytes, back_image_bytes=back_bytes,
            front_image_span=front_span, back_image_span=back_span,
        ))

    for offset, record_type, text, image_span in _records(content, prefixed, encoding):
        if record_type == "25":
            flush()
            amount_raw = text[_T25_AMOUNT].strip()
            try:
                cents = int(amount_raw or "0")
            except ValueError:
                raise X937ParseError(
                    f"Check Detail Record #{len(items) + 1} at byte {offset} has a non-numeric amount field: {amount_raw!r}"
                ) from None
            on_us = text[_T25_ON_US].strip()
            account, check = split_on_us(on_us, text[_T25_AUX_ON_US])
            current = {
                "item_number": len(items) + 1, "routing_number": text[_T25_ROUTING_NUMBER].strip(),
                "account_number": account, "check_number": check, "amount": Decimal(cents) / Decimal(100),
                "presented_date": business_date, "on_us": on_us, "images": [],
            }
            for totals in (bundle, letter, whole):
                totals[0] += 1
                totals[1] += cents
        elif record_type == "52":
            if current is not None:
                current["images"].append(image_span)
        elif record_type == "10":
            business_date = _parse_business_date(text[_T10_BUSINESS_DATE].strip())
            letter[:] = [0, 0]
        elif record_type == "20":
            bundle[:] = [0, 0]
        elif record_type == "70":
            _check_totals(text, _T70_ITEMS_COUNT, _T70_TOTAL_AMOUNT, items=bundle[0], cents=bundle[1], offset=offset, scope="Bundle")
        elif record_type == "90":
            _check_totals(text, _T90_ITEMS_COUNT, _T90_TOTAL_AMOUNT, items=letter[0], cents=letter[1], offset=offset, scope="Cash Letter")
        elif record_type == "99":
            _check_totals(text, _T99_ITEMS_COUNT, _T99_TOTAL_AMOUNT, items=whole[0], cents=whole[1], offset=offset, scope="File")
        elif record_type in _RETURN_RECORD_TYPES:
            raise X937ParseError(
                f"Return record (type {record_type}) at byte {offset}: this is a return cash letter, which OpenClear "
                "doesn't import. Upload forward presentment files only."
            )
        elif record_type not in _SKIP_RECORD_TYPES:
            raise X937ParseError(f"Unrecognized record type {record_type!r} at byte {offset}")

    flush()
    return items
