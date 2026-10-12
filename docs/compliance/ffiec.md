# FFIEC IT Examination Handbook

How OpenClear supports the areas the FFIEC IT Examination Handbook booklets examine, chiefly
*Information Security*, *Architecture, Infrastructure, and Operations*, *Development,
Acquisition, and Maintenance*, and *Audit*. The booklets are principles-based. This page maps
OpenClear's controls onto their themes; it doesn't cite individual sections. It isn't legal
advice ([Compliance](README.md)).

## Access control and identity

| Theme | What OpenClear does | Evidence |
|---|---|---|
| Least privilege | Per-bank security groups built from fine-grained permissions, checked on every API and web request. Business customers are scoped to their own accounts. | Settings → Security groups; Users → Export |
| Segregation of duties | Two default rules: whoever submits items can't decide them, and whoever manages users can't define what groups grant. Enforced on groups and per item: the person who presented an item can't decide it. In *enforce* mode a breach is refused (`403 ERR_SOD_VIOLATION`); in either mode it's logged. | Settings → Segregation of duties; `sod.violation` entries in the action log; Controls |
| Dual control | Maker-checker on pay/return decisions, per bank. Changes to matching rules and scoring models need a second admin under enforce mode. | Settings; Settings → Changes; the evidence bundle's changes |
| Authentication | bcrypt passwords with a per-bank policy; FIDO2/WebAuthn security keys and passkeys; SSO with OIDC or SAML 2.0, group mapping, SCIM provisioning and sign-out from the identity provider. Systems use OAuth 2.0 client credentials, not passwords. | Controls → "Admins use a second factor"; Users → Export |
| Access recertification | Every membership with its group, scope, status and last sign-in, exportable. | Users → Export (CSV, JSON); `GET /api/v1/users` |

## Data protection

| Theme | What OpenClear does | Evidence |
|---|---|---|
| Tenant isolation | On Postgres, row-level security on every bank's tables, bound to the bank on each transaction. | `tests/test_postgres` in the source |
| Secrets at rest | Secrets stored in the database (SSO client secrets, the SAML signing key, webhook secrets, secret plugin settings) are AES-256-GCM, versioned by key, and re-encrypted when the key rotates. | Controls → "Secrets encrypted in the database" |
| Disk and database encryption | The host's job. The operator records how it's done. | Controls → "Disk and database encryption" |
| Integrity of files | Every uploaded file is kept with its SHA-256 and an ECDSA signature. | Bulk upload details |
| Data in transit | HTTPS at the reverse proxy; secure, `SameSite` cookies; a strict Content-Security-Policy. | README, "Reverse proxy / WAF deployment" |

## Logging and monitoring

| Theme | What OpenClear does | Evidence |
|---|---|---|
| Audit trail | Every state-changing action, by person or system, with time, channel and details. Each entry is ECDSA-signed and chained to the one before, so an edit, deletion or reordering is detectable. Old entries can move to signed archives without breaking the chain. | Settings → Action log → Verify; Controls → "Action log intact" |
| Log management | Security events (sign-ins and their failures, lockouts, second factors, API tokens, refusals, and changes to who may do what), each with the caller's address, the request and who, and never a secret or account data. Delivered to the bank's SIEM (syslog over TLS as JSON, CEF or LEEF; Splunk HEC; HTTPS) in order and at least once, with retries, from the bank's own destination and the installation's; also readable through a pull API. Events a destination hasn't received are kept until it has. JSON operational logs carry the same request context. | Settings → Security event forwarding; Controls → "Security events delivered"; the evidence bundle's `security_events`; `docs/guides/siem.md` |
| Continuous monitoring | Live controls with PASS / WARN / FAIL and evidence, by API for a bank's monitoring tools. `openclear controls` exits non-zero on a failure. | Settings → Controls |
| Timeliness | Exceptions past or near their decision deadline, stuck imports, a late decision outbox. | Controls → "Decisions and imports on time" |

## Development, change and resilience

| Theme | What OpenClear does | Evidence |
|---|---|---|
| Secure development | An automated suite of about 1,400 tests on SQLite, Postgres and Linux before each release. A published API contract that changes only additively. A written security review with fixes. | `SECURITY_REVIEW.md`; `CHANGELOG.md`; CI |
| Change control in production | Rules and models in force, with a SHA-256 of each, in every evidence export. Two-person approval for rule and model changes. Database upgrades and downgrades are versioned. | The evidence bundle's `rules` and `models` |
| Input handling | A corpus of malformed X9.37 and NACHA files run through the real parsers, which must refuse each cleanly and quickly. | Controls → "Malformed files refused" |
| Production safety | Refuses to start in production with development keys or unsafe options. | Controls → "Production settings" |
| Exit | A full export of a bank's data, for migration or offboarding. | Settings → Data export |
