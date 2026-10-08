# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

"""The X9.37 image cash letter parser (bulk_import/x937.py): the standard layout in every
framing and both encodings, control totals, and Moov's reference files
(tests/fixtures/moov/), whose expected values are what Moov's own reader returns."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from openclear.bulk_import.x937 import X937ParseError, item_images, parse_x937_file, split_on_us
from tests.x937_builder import FRAMINGS, Check, _record, build, single

MOOV = Path(__file__).resolve().parent.parent / "fixtures" / "moov"
FRONT, BACK = b"\xff\xd8fake-jpeg-front\r\n\x00", b"\xff\xd8fake-jpeg-back"


# --- Moov's reference files ------------------------------------------------------------


@pytest.mark.parametrize("name", ["valid-ebcdic.x937", "valid-ascii.x937"])
def test_moovs_sample_reads_as_moov_reads_it(name):
    data = (MOOV / name).read_bytes()
    [item] = parse_x937_file(data)
    # Moov v0.17.0: payorBankRoutingNumber 12200066, onUs "1211-1234-56789/", auxiliaryOnUs "",
    # itemAmount 10000, two image views of 7,408 and 8,646 bytes, cash letter business date 2020-10-23.
    assert (item.routing_number, item.on_us, item.amount, item.presented_date) == ("12200066", "1211-1234-56789/", Decimal("100.00"), date(2020, 10, 23))
    assert (item.account_number, item.check_number) == ("1211123456789", "")
    assert (len(item.front_image_bytes), len(item.back_image_bytes)) == (7408, 8646)
    assert item.front_image_bytes[:2] == b"II"  # a little-endian TIFF, untouched by decoding


def test_moov_ascii_and_ebcdic_agree_byte_for_byte_on_images():
    ascii_item, = parse_x937_file((MOOV / "valid-ascii.x937").read_bytes())
    ebcdic_item, = parse_x937_file((MOOV / "valid-ebcdic.x937").read_bytes())
    assert ascii_item.front_image_bytes == ebcdic_item.front_image_bytes


def test_a_return_cash_letter_is_rejected():
    with pytest.raises(X937ParseError, match="(?i)control|return"):
        parse_x937_file((MOOV / "BNK20180905121042882-A.icl").read_bytes())


# --- Framing and encoding ----------------------------------------------------------------


@pytest.mark.parametrize("encoding", ["ascii", "cp037"])
@pytest.mark.parametrize("framing", FRAMINGS)
def test_every_framing_and_encoding(framing, encoding):
    data = single("1001/", "5001", 15000, [FRONT, BACK], framing=framing, encoding=encoding)
    [item] = parse_x937_file(data)
    assert (item.routing_number, item.account_number, item.check_number, item.amount) == ("12345678", "1001", "5001", Decimal("150.00"))
    assert item.presented_date == date(2026, 1, 15)
    assert (item.front_image_bytes, item.back_image_bytes) == (FRONT, BACK)  # binary, including CR/LF bytes


@pytest.mark.parametrize("framing", FRAMINGS)
def test_lazy_images_match(framing):
    data = single("1001/", "5001", 100, [FRONT, BACK], framing=framing)
    [eager], [lazy] = parse_x937_file(data), parse_x937_file(data, lazy_images=True)
    assert lazy.front_image_bytes == b"" and item_images(lazy, data) == (FRONT, BACK) == item_images(eager)


def test_not_an_x937_file():
    with pytest.raises(X937ParseError, match="File Header"):
        parse_x937_file(b"account_number,check_number\n1,2\n")


# --- What's extracted ---------------------------------------------------------------------


@pytest.mark.parametrize(("on_us", "aux", "expected"), [
    ("1001/", "5001", ("1001", "5001")),             # business check: serial in Auxiliary On-Us
    ("1211-1234 56789/", "", ("1211123456789", "")),  # dashes and spaces are MICR spacing
    ("7654321/1234", "", ("7654321", "1234")),        # personal check: serial after the on-us symbol
    ("7654321", "", ("7654321", "")),                 # no symbol at all
])
def test_on_us_split(on_us, aux, expected):
    assert split_on_us(on_us, aux) == expected


def test_multiple_checks_in_one_cash_letter():
    data = build([[[Check("1001/", "5001", 10000, images=[FRONT]), Check("1002/", "5002", 2550)]]])
    items = parse_x937_file(data)
    assert [(i.item_number, i.account_number, i.check_number, i.amount) for i in items] == [
        (1, "1001", "5001", Decimal("100.00")), (2, "1002", "5002", Decimal("25.50"))]
    assert items[0].front_image_bytes == FRONT and items[0].back_image_bytes is None


def test_a_check_with_no_images_gets_empty_front_bytes():
    [item] = parse_x937_file(single("1001/", "5001", 100))
    assert item.front_image_bytes == b"" and item.back_image_bytes is None


def test_image_analysis_and_addendum_records_are_skipped():
    extra = {1: [_record("26", [(2, 1, "1")]), _record("28", [(2, 2, "01")])]}
    data = build([[[Check("1001/", "5001", 100, images=[FRONT])]]], extra_records=extra)
    assert len(parse_x937_file(data)) == 1


def test_return_records_are_rejected():
    data = build([[[Check("1001/", "5001", 100)]]], extra_records={1: [_record("31")]})
    with pytest.raises(X937ParseError, match="return cash letter"):
        parse_x937_file(data)


def test_blank_business_date_falls_back_to_today():
    [item] = parse_x937_file(single("1001/", "5001", 100, business_date="        "))
    assert item.presented_date == date.today()


# --- Structural problems --------------------------------------------------------------------


def test_unrecognized_record_type_raises():
    data = build([[[Check("1001/", "5001", 100)]]], extra_records={1: [_record("77")]})
    with pytest.raises(X937ParseError, match="Unrecognized record type '77'"):
        parse_x937_file(data)


@pytest.mark.parametrize("framing", FRAMINGS)
def test_truncated_file_raises(framing):
    data = single("1001/", "5001", 100, [FRONT], framing=framing)
    with pytest.raises(X937ParseError):
        parse_x937_file(data[: len(data) - 30])


def test_non_numeric_amount_raises():
    data = build([[[Check("1001/", "5001", amount_text="12AB000000")]]])
    with pytest.raises(X937ParseError, match="non-numeric amount"):
        parse_x937_file(data)


def test_image_length_beyond_the_record_raises():
    data = bytearray(single("1001/", "5001", 100, [FRONT], framing="prefixed"))
    marker = str(len(FRONT)).zfill(7).encode()
    position = data.index(b"0000" + b"00000" + marker) + 9
    data[position : position + 7] = b"9999999"
    with pytest.raises(X937ParseError, match="doesn't fit"):
        parse_x937_file(bytes(data))


# --- Control totals ----------------------------------------------------------------------------


@pytest.mark.parametrize(("record", "scope"), [("70", "Bundle"), ("90", "Cash Letter"), ("99", "File")])
@pytest.mark.parametrize("which", ["items", "cents"])
def test_control_total_mismatches_raise(record, scope, which):
    data = build([[[Check("1001/", "5001", 100)]]], declared={(record, 1): {which: 7}})
    with pytest.raises(X937ParseError, match=f"{scope} Control Record"):
        parse_x937_file(data)


def test_blank_bundle_control_is_rejected():
    data = build([[[Check("1001/", "5001", 100)]]], declared={("70", 1): {"items": "    ", "cents": " " * 12}})
    with pytest.raises(X937ParseError, match="Bundle Control Record"):
        parse_x937_file(data)


def test_totals_reset_between_bundles_and_cash_letters():
    letters = [
        [[Check("1001/", "1", 100)], [Check("1001/", "2", 200), Check("1001/", "3", 300)]],
        [[Check("1001/", "4", 400)]],
    ]
    assert [i.check_number for i in parse_x937_file(build(letters))] == ["1", "2", "3", "4"]


def test_a_large_file_total_uses_all_sixteen_digits():
    # The File Control total is 16 digits; reading 15 would drop the last one.
    letters = [[[Check("1001/", str(n), 99_999_999) for n in range(12)]]]
    items = parse_x937_file(build(letters))
    assert sum(i.amount for i in items) == Decimal("11999999.88")
