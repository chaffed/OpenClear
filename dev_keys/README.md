# Dev/test keys — DO NOT USE IN PRODUCTION

These six PEM files are a deliberately public, checked-in ECDSA P-256 key pair set
(one pair each for JWT signing, bulk-upload file signing and audit-log signing) so local development and the test suite work with zero setup —
exactly the role the old hardcoded `"dev-secret-change-me-..."` strings used to play,
just as real (but published, hence insecure) keys instead of a guessable string.

Anyone who has cloned this repository has these private keys. Using them for a real
deployment would let anyone forge login sessions, file signatures, or audit log
entries.

`config.py::assert_production_safe` refuses to start the app with
`OPENCLEAR_ENVIRONMENT=production` while any of the `*_private_key_path`/
`*_public_key_path` settings still point here. Generate your own key pair with:

```
python scripts/generate_keys.py --output-dir keys
```

See the root `README.md`'s "Signing keys" section for the full picture, including a
manual `openssl` equivalent if you'd rather not run the script.
