# Embedding example: a stand-in for online banking

This is a page that plays your online banking's part. Its server asks OpenClear for a launch URL on behalf of a person, and the page shows OpenClear with [`@openclear/embed`](../../sdks/embed). The guide is [docs/guides/embedding.md](../../docs/guides/embedding.md).

```sh
cd sdks/embed && npm install && npm run build && cd -
pip install -e sdks/python
OPENCLEAR_URL=https://openclear.yourbank.example \
OPENCLEAR_CLIENT_ID=occ_... OPENCLEAR_CLIENT_SECRET=... \
PERSON_EMAIL=pat@acme.example \
python examples/embed-host/server.py   # then open https://localhost:8443 (see below)
```

The example has these requirements:

- **An API client allowed to act for your users.** It acts for `PERSON_EMAIL` with `OpenClear-Act-As`. A real online banking platform would use token exchange with the person's ID token instead.
- **The host's origin on the bank's allowlist:** add `https://localhost:8443` under **Settings → Embedding**.
- **HTTPS on both sides.** OpenClear's embedded cookies are `Secure`. Give the server a certificate with `CERT=cert.pem KEY=key.pem`, for example one from `mkcert localhost`.
