# Moov X9.37 sample files

From [moov-io/imagecashletter](https://github.com/moov-io/imagecashletter) `test/testdata/`
(commit `3ef5f61141c3bc6d4df2d02e85215f5d1749d59e`), Copyright The Moov Authors, under the Apache License 2.0 (`LICENSE` here).
Unmodified.

Used by `tests/test_bulk_import/test_x937.py` to check OpenClear's X9.37 parser against a
reference implementation. The expected values in that test are what Moov's own reader
returns for these files (its server, `moov/imagecashletter` v0.17.0).

- `valid-ebcdic.x937`, `valid-ascii.x937`: the same one-check cash letter, length-prefixed
  records, in EBCDIC and ASCII.
- `BNK20180905121042882-A.icl`: ASCII, with return records and bundle counts that count
  records rather than items; OpenClear rejects it (it doesn't import return cash letters).
