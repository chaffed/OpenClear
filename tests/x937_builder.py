# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""Builds X9.37 image cash letter files for tests, in the standard DSTU X9.37 layout (the
same one bulk_import/x937.py reads, checked against Moov's reference files in
tests/fixtures/moov/). Any framing and encoding the parser accepts:

    build([[Check(on_us="1001/", aux_on_us="5001", amount_cents=15000, images=[front, back])]])

`letters` is a list of cash letters, each a list of bundles, each a list of checks. Control
totals are computed; `declared` overrides one to test a mismatch, keyed by (record type,
occurrence) with values {"items": n, "cents": n}."""

from dataclasses import dataclass, field

FRAMINGS = ("prefixed", "crlf", "plain")


@dataclass
class Check:
    on_us: str
    aux_on_us: str = ""
    amount_cents: int = 0
    routing: str = "12345678"
    images: list[bytes] = field(default_factory=list)
    amount_text: str | None = None  # a raw amount field, to test a bad one


def _record(record_type: str, fields: list[tuple[int, int, str]] = (), length: int = 80) -> str:
    """A fixed-width record (80 characters unless given): `fields` are (start, length,
    value), start 0-based."""
    chars = list(record_type + " " * (length - 2))
    for start, length, value in fields:
        chars[start : start + length] = list(value.ljust(length)[:length])
    return "".join(chars)


def _image_record(image: bytes, encoding: str) -> bytes:
    fixed = _record("52", [(2, 9, "123456780"), (11, 8, "20260115")], length=101)
    return (fixed + "0000" + "00000" + str(len(image)).zfill(7)).encode(encoding) + image


def build(
    letters: list[list[list[Check]]],
    *,
    business_date: str = "20260115",
    framing: str = "prefixed",
    encoding: str = "ascii",
    declared: dict | None = None,
    extra_records: dict[int, list[str]] | None = None,
) -> bytes:
    """`extra_records` inserts raw 80-character records after the check with that 1-based
    number (to test record types the parser skips or rejects)."""
    declared = declared or {}
    records: list[bytes] = []
    occurrences: dict[str, int] = {}

    def text(record: str) -> None:
        records.append(record.encode(encoding))

    def control(record_type: str, items: int, cents: int, items_field: tuple[int, int], amount_field: tuple[int, int]) -> None:
        occurrences[record_type] = occurrences.get(record_type, 0) + 1
        override = declared.get((record_type, occurrences[record_type]), {})
        items, cents = override.get("items", items), override.get("cents", cents)
        items_text = items if isinstance(items, str) else str(items).zfill(items_field[1])
        cents_text = cents if isinstance(cents, str) else str(cents).zfill(amount_field[1])
        text(_record(record_type, [(*items_field, items_text), (*amount_field, cents_text)]))

    file_items = file_cents = check_number = 0
    text(_record("01", [(2, 2, "03"), (4, 1, "T")]))
    for bundles in letters:
        text(_record("10", [(2, 2, "01"), (4, 9, "123456780"), (13, 9, "123456780"), (22, 8, business_date)]))
        letter_items = letter_cents = 0
        for checks in bundles:
            text(_record("20", [(2, 2, "01")]))
            bundle_items = bundle_cents = 0
            for check in checks:
                check_number += 1
                amount = check.amount_text if check.amount_text is not None else str(check.amount_cents).zfill(10)
                text(_record("25", [(2, 15, check.aux_on_us.rjust(15)), (18, 8, check.routing), (26, 1, "0"),
                                    (27, 20, check.on_us.rjust(20)), (47, 10, amount)]))
                for record in (extra_records or {}).get(check_number, []):
                    text(record)
                for image in check.images:
                    text(_record("50", [(2, 1, "1")]))
                    records.append(_image_record(image, encoding))
                bundle_items += 1
                bundle_cents += check.amount_cents
            control("70", bundle_items, bundle_cents, (2, 4), (6, 12))
            letter_items += bundle_items
            letter_cents += bundle_cents
        control("90", letter_items, letter_cents, (8, 8), (16, 14))
        file_items += letter_items
        file_cents += letter_cents
    control("99", file_items, file_cents, (16, 8), (24, 16))

    if framing == "prefixed":
        return b"".join(len(r).to_bytes(4, "big") + r for r in records)
    if framing == "crlf":
        return b"\r\n".join(records) + b"\r\n"
    if framing == "plain":
        return b"".join(records)
    raise ValueError(f"framing must be one of {FRAMINGS}")


def single(on_us: str, aux_on_us: str, amount_cents: int, images: list[bytes] = (), **kwargs) -> bytes:
    """A file with one check, the common case."""
    return build([[[Check(on_us=on_us, aux_on_us=aux_on_us, amount_cents=amount_cents, images=list(images))]]], **kwargs)
