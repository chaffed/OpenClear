# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import json
from decimal import Decimal
from pathlib import Path

import pytest

from openclear.bulk_import.nacha import NachaParseError, parse_nacha_file, transaction_type_for_code

_ENTRY_HASH_MODULUS = 10**10
_CREDIT_CODES = {"22", "23", "24", "32", "33", "34"}
_DEBIT_CODES = {"27", "28", "29", "37", "38", "39"}


def _batch_header(*, company_name="ACME CORP", company_id="1234567890", sec_code="PPD", effective_date="260115") -> str:
    return (
        "5"
        + "200"
        + company_name.ljust(16)[:16]
        + " " * 20
        + company_id.ljust(10)[:10]
        + sec_code.ljust(3)[:3]
        + "PAYROLL   "  # company entry description, 10 chars
        + "260114"  # company descriptive date, 6 chars
        + effective_date
        + "   "  # settlement date (julian), 3 chars
        + "1"  # originator status code
        + "12345678"  # originating DFI id, 8 chars
        + "0000001"  # batch number, 7 chars
    )


def _entry_detail(
    *,
    transaction_code="22",
    receiving_dfi_id="12345678",
    dfi_account_number="000123456789",
    amount_cents="0000015000",
    individual_id="EMP001",
    trace_number="123456780000001",
) -> str:
    return (
        "6"
        + transaction_code
        + receiving_dfi_id.rjust(8, "0")[:8]
        + "1"  # check digit
        + dfi_account_number.ljust(17)[:17]
        + amount_cents.rjust(10, "0")[:10]
        + individual_id.ljust(15)[:15]
        + "JOHN DOE".ljust(22)[:22]
        + "  "  # discretionary data
        + "0"  # addenda record indicator
        + trace_number.ljust(15)[:15]
    )


def _entry_spec(*, transaction_code="22", receiving_dfi_id="12345678", amount_cents="0000015000") -> dict:
    """Tracks exactly what a matching _entry_detail(...) call needs for control-total
    computation below, so a test never has to hand-calculate an entry hash or dollar
    total — it's derived from the same values passed to _entry_detail."""
    return {"transaction_code": transaction_code, "receiving_dfi_id": receiving_dfi_id, "amount_cents": int(amount_cents)}


def _control_totals(entries: list[dict]) -> dict:
    return {
        "count": len(entries),
        "hash": sum(int(e["receiving_dfi_id"]) for e in entries) % _ENTRY_HASH_MODULUS,
        "debit": sum(e["amount_cents"] for e in entries if e["transaction_code"] in _DEBIT_CODES),
        "credit": sum(e["amount_cents"] for e in entries if e["transaction_code"] in _CREDIT_CODES),
    }


def _batch_control(entries: list[dict], **overrides) -> str:
    totals = {**_control_totals(entries), **overrides}
    return (
        "8"
        + "200"
        + str(totals["count"]).rjust(6, "0")
        + str(totals["hash"]).rjust(10, "0")
        + str(totals["debit"]).rjust(12, "0")
        + str(totals["credit"]).rjust(12, "0")
        + " " * 50
    )


def _file_control(entries: list[dict], **overrides) -> str:
    totals = {**_control_totals(entries), **overrides}
    return (
        "9"
        + "000001"
        + "000001"
        + str(totals["count"]).rjust(8, "0")
        + str(totals["hash"]).rjust(10, "0")
        + str(totals["debit"]).rjust(12, "0")
        + str(totals["credit"]).rjust(12, "0")
        + " " * 39
    )


def test_parses_single_batch_single_entry():
    entries = [_entry_spec()]
    lines = [_batch_header(), _entry_detail(), _batch_control(entries)]
    entries_out = parse_nacha_file("\n".join(lines).encode("ascii"))

    assert len(entries_out) == 1
    entry = entries_out[0]
    assert entry.company_id == "1234567890"
    assert entry.company_name == "ACME CORP"
    assert entry.sec_code == "PPD"
    assert entry.effective_date.isoformat() == "2026-01-15"
    assert entry.dfi_account_number == "000123456789"
    assert entry.amount == 150
    assert entry.individual_id == "EMP001"
    assert entry.transaction_code == "22"
    assert entry.trace_number == "123456780000001"


def test_multiple_entries_share_the_same_batch_header_context():
    specs = [
        _entry_spec(),
        _entry_spec(amount_cents="0000099999"),
    ]
    lines = [
        _batch_header(company_id="9999999999"),
        _entry_detail(trace_number="000000000000001"),
        _entry_detail(trace_number="000000000000002", amount_cents="0000099999"),
        _batch_control(specs),
    ]
    entries = parse_nacha_file("\n".join(lines).encode("ascii"))

    assert len(entries) == 2
    assert all(e.company_id == "9999999999" for e in entries)
    assert entries[1].amount == Decimal("999.99")
    assert entries[0].trace_number == "000000000000001"
    assert entries[1].trace_number == "000000000000002"


def test_multiple_batches_with_different_originators():
    specs = [_entry_spec()]
    lines = [
        _batch_header(company_id="1111111111", company_name="FIRST CO"),
        _entry_detail(trace_number="000000000000001"),
        _batch_control(specs),
        _batch_header(company_id="2222222222", company_name="SECOND CO"),
        _entry_detail(trace_number="000000000000002"),
        _batch_control(specs),
    ]
    entries = parse_nacha_file("\n".join(lines).encode("ascii"))

    assert len(entries) == 2
    assert entries[0].company_id == "1111111111"
    assert entries[1].company_id == "2222222222"


def test_entry_before_any_batch_header_is_an_error():
    lines = [_entry_detail()]
    with pytest.raises(NachaParseError, match="before any batch header"):
        parse_nacha_file("\n".join(lines).encode("ascii"))


def test_missing_dfi_account_number_is_an_error():
    lines = [_batch_header(), _entry_detail(dfi_account_number=""), _batch_control([_entry_spec()])]
    with pytest.raises(NachaParseError, match="no DFI account number"):
        parse_nacha_file("\n".join(lines).encode("ascii"))


def test_empty_file_with_no_entries_is_an_error():
    with pytest.raises(NachaParseError, match="No entry detail records"):
        parse_nacha_file(b"")


def test_invalid_effective_date_is_an_error():
    lines = [_batch_header(effective_date="269999"), _entry_detail(), _batch_control([_entry_spec()])]
    with pytest.raises(NachaParseError, match="invalid effective date"):
        parse_nacha_file("\n".join(lines).encode("ascii"))


@pytest.mark.parametrize("code,expected", [("22", "credit"), ("27", "debit"), ("32", "credit"), ("37", "debit")])
def test_transaction_type_for_code(code, expected):
    assert transaction_type_for_code(code) == expected


def test_unrecognized_transaction_code_raises():
    with pytest.raises(NachaParseError, match="Unrecognized"):
        transaction_type_for_code("99")


def test_short_unpadded_lines_are_tolerated():
    # A file header record we don't care about, deliberately short/malformed — lenient
    # mode should still extract the entry detail correctly. (Batch/file control records
    # ARE validated now, so this no longer uses a bogus type-9 filler line the way it
    # once did — see test_file_control_* below for that coverage.)
    entries = [_entry_spec()]
    lines = ["1 short file header", _batch_header(), _entry_detail(), _batch_control(entries)]
    parsed = parse_nacha_file("\n".join(lines).encode("ascii"))
    assert len(parsed) == 1


def test_correct_batch_control_totals_parse_cleanly():
    entries = [_entry_spec(transaction_code="22", amount_cents="0000015000"), _entry_spec(transaction_code="27", amount_cents="0000005000")]
    lines = [
        _batch_header(),
        _entry_detail(transaction_code="22", amount_cents="0000015000", trace_number="000000000000001"),
        _entry_detail(transaction_code="27", amount_cents="0000005000", trace_number="000000000000002"),
        _batch_control(entries),
    ]
    parsed = parse_nacha_file("\n".join(lines).encode("ascii"))
    assert len(parsed) == 2


@pytest.mark.parametrize("field,override", [("entry/addenda count", {"count": 99}), ("entry hash", {"hash": 1}), ("total debit amount", {"debit": 1}), ("total credit amount", {"credit": 1})])
def test_batch_control_mismatch_raises(field, override):
    entries = [_entry_spec(transaction_code="27")]
    lines = [_batch_header(), _entry_detail(transaction_code="27"), _batch_control(entries, **override)]
    with pytest.raises(NachaParseError, match=field):
        parse_nacha_file("\n".join(lines).encode("ascii"))


def test_correct_file_control_totals_parse_cleanly():
    entries = [_entry_spec()]
    lines = [_batch_header(), _entry_detail(), _batch_control(entries), _file_control(entries)]
    parsed = parse_nacha_file("\n".join(lines).encode("ascii"))
    assert len(parsed) == 1


def test_file_control_totals_sum_across_multiple_batches():
    spec_a = _entry_spec(transaction_code="22", amount_cents="0000010000")
    spec_b = _entry_spec(transaction_code="27", amount_cents="0000002500")
    lines = [
        _batch_header(company_id="1111111111"),
        _entry_detail(transaction_code="22", amount_cents="0000010000", trace_number="000000000000001"),
        _batch_control([spec_a]),
        _batch_header(company_id="2222222222"),
        _entry_detail(transaction_code="27", amount_cents="0000002500", trace_number="000000000000002"),
        _batch_control([spec_b]),
        _file_control([spec_a, spec_b]),
    ]
    parsed = parse_nacha_file("\n".join(lines).encode("ascii"))
    assert len(parsed) == 2


@pytest.mark.parametrize("field,override", [("entry/addenda count", {"count": 99}), ("entry hash", {"hash": 1}), ("total debit amount", {"debit": 1}), ("total credit amount", {"credit": 1})])
def test_file_control_mismatch_raises(field, override):
    entries = [_entry_spec(transaction_code="27")]
    lines = [_batch_header(), _entry_detail(transaction_code="27"), _batch_control(entries), _file_control(entries, **override)]
    with pytest.raises(NachaParseError, match=field):
        parse_nacha_file("\n".join(lines).encode("ascii"))


# --- Moov's reference files (tests/fixtures/moov-ach/) ------------------------------------


MOOV = Path(__file__).resolve().parent.parent / "fixtures" / "moov-ach"
_MOOV_EXPECTED = json.loads((MOOV / "expected.json").read_text())


@pytest.mark.parametrize("name", sorted(_MOOV_EXPECTED))
def test_moov_samples_read_as_moov_reads_them(name):
    entries = parse_nacha_file((MOOV / name).read_bytes())
    expected = _MOOV_EXPECTED[name]
    assert len(entries) == len(expected)
    for entry, want in zip(entries, expected):
        assert entry.dfi_account_number == want["account"]
        assert entry.amount == Decimal(want["cents"]) / 100
        assert entry.transaction_code == want["transaction_code"]
        assert entry.trace_number.lstrip("0") == want["trace"].lstrip("0")
        assert (entry.sec_code, entry.company_id) == (want["sec"], want["company_id"])
        if want["company_name"] is not None:
            assert entry.company_name == want["company_name"]
        if want["individual_id"] is not None:
            assert (entry.individual_id or "") == want["individual_id"]


def test_moov_adv_file_is_rejected():
    with pytest.raises(NachaParseError, match="ADV"):
        parse_nacha_file((MOOV / "adv.ach").read_bytes())


@pytest.mark.parametrize(("code", "expected"), [("47", "debit"), ("42", "credit"), ("55", "debit"), ("52", "credit"), ("26", "debit"), ("21", "credit")])
def test_general_ledger_loan_and_return_codes(code, expected):
    assert transaction_type_for_code(code) == expected
