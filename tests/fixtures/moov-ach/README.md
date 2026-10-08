# Moov ACH sample files

From [moov-io/ach](https://github.com/moov-io/ach) `test/testdata/` (commit `d96bcf2183da5cd57371d97320ccd91ca49a3392`),
Copyright The Moov Authors, under the Apache License 2.0 (`LICENSE` here). Unmodified.

`expected.json` is what Moov's own reader (the `moov/ach` server) returns for each file's
entries; `tests/test_bulk_import/test_nacha.py` checks OpenClear's parser against it. The
samples cover PPD, WEB, TEL, CCD-style mixed debits and credits, general ledger, IAT,
RCK, TXP, returns, a notification of change, a file without line breaks, and lines too
short or too long. `adv.ach` (an accounting advice file) is there to show it's rejected.
