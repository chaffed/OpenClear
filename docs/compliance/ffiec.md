# FFIEC IT Examination Handbook

How OpenClear supports the areas the FFIEC IT Examination Handbook booklets examine, chiefly
*Information Security*, *Architecture, Infrastructure, and Operations*, *Development,
Acquisition, and Maintenance*, and *Audit*. The booklets are principles-based. This page maps
OpenClear's controls onto their themes, each with its [control id](controls.md) and NIST SP
800-53 Rev. 5 controls; it doesn't cite the booklets' sections. It isn't legal
advice ([Compliance](README.md)).

## Access control and identity

| Theme | What OpenClear does | Controls (NIST SP 800-53) | Evidence |
|---|---|---|---|
| Least privilege | Per-bank security groups built from fine-grained permissions, checked on every API and web request. Business customers are scoped to their own accounts. | OC-AC-01: AC-3, AC-6<br>OC-AC-03: AC-3, AC-4, AC-6<br>OC-AC-04: AC-3, AC-6 | Settings → Security groups; Users → Export |
| Segregation of duties | Two default rules: whoever submits items can't decide them, and whoever manages users can't define what groups grant. Enforced on groups and per item: the person who presented an item can't decide it. In *enforce* mode a breach is refused (`403 ERR_SOD_VIOLATION`); in either mode it's logged. | OC-AC-05: AC-5 | Settings → Segregation of duties; `sod.violation` entries in the action log; Controls |
| Dual control | Maker-checker on pay/return decisions, per bank. Changes to matching rules and scoring models need a second admin under enforce mode. | OC-AC-06: AC-3(2), AC-5<br>OC-AC-07: CM-3, CM-5, AC-3(2) | Settings; Settings → Changes; the evidence bundle's changes |
| Authentication | bcrypt passwords with a per-bank policy; FIDO2/WebAuthn security keys and passkeys; SSO with OIDC or SAML 2.0, group mapping, SCIM provisioning and sign-out from the identity provider. Systems use OAuth 2.0 client credentials, not passwords. | OC-IA-01: IA-5(1), AC-7<br>OC-IA-02: IA-2(1), IA-2(2), IA-2(8)<br>OC-IA-03: IA-2, IA-8, AC-2(1)<br>OC-IA-04: IA-9, IA-5<br>OC-IA-05: IA-8 | Controls → "Admins use a second factor"; Users → Export |
| Vendor and support access | No impersonation: the platform's support staff get into a bank only through access the bank grants, to a named person, for a stated reason and period, read-only unless a second bank admin approves write access, and never to decide items or change who may do what. They work under their own name with a banner; access ends on time or when the bank ends it. Nobody grants their own access. | OC-AC-08: AC-3, MA-4, AC-6(5) | Settings → Support access; `support_access.*` in the action log and security events; the evidence bundle's `support_access` |
| Access recertification | Every membership with its group, scope, status and last sign-in, exportable. | OC-AC-02: AC-2, AC-2(3), AC-2(1) | Users → Export (CSV, JSON); `GET /api/v1/users` |

## Data protection

| Theme | What OpenClear does | Controls (NIST SP 800-53) | Evidence |
|---|---|---|---|
| Tenant isolation | On Postgres, row-level security on every bank's tables, bound to the bank on each transaction. | OC-AC-09: AC-4, AC-3 | `tests/test_postgres` in the source |
| Secrets at rest | Secrets stored in the database (SSO client secrets, the SAML signing key, webhook secrets, secret plugin settings) are AES-256-GCM, versioned by key, and re-encrypted when the key rotates. | OC-SC-01: SC-28(1), SC-12, SC-13 | Controls → "Secrets encrypted in the database" |
| Disk and database encryption | The host's job. The operator records how it's done. | OC-SC-02: SC-28 | Controls → "Disk and database encryption" |
| Integrity of files | Every uploaded file is kept with its SHA-256 and an ECDSA signature. | OC-SC-03: SI-7(6), SC-8 | Bulk upload details |
| Data in transit | HTTPS at the reverse proxy; secure, `SameSite` cookies; a strict Content-Security-Policy. | OC-SC-04: SC-23, SC-8, SC-18<br>OC-SC-05: SC-7 | README, "Reverse proxy / WAF deployment" |

## Logging and monitoring

| Theme | What OpenClear does | Controls (NIST SP 800-53) | Evidence |
|---|---|---|---|
| Audit trail | Every state-changing action, by person or system, with time, channel and details. Each entry is ECDSA-signed and chained to the one before, so an edit, deletion or reordering is detectable. Old entries can move to signed archives without breaking the chain. | OC-AU-01: AU-2, AU-3, AU-9(3), AU-10, AU-12<br>OC-AU-02: AU-9, SI-7<br>OC-AU-03: AU-11, AU-9(3) | Settings → Action log → Verify; Controls → "Action log intact" |
| Log management | Security events (sign-ins and their failures, lockouts, second factors, API tokens, refusals, and changes to who may do what), each with the caller's address, the request and who, and never a secret or account data. Delivered to the bank's SIEM (syslog over TLS as JSON, CEF or LEEF; Splunk HEC; HTTPS) in order and at least once, with retries, from the bank's own destination and the installation's; also readable through a pull API. Events a destination hasn't received are kept until it has. JSON operational logs carry the same request context. | OC-AU-04: AU-12, AU-6(1), SI-4 | Settings → Security event forwarding; Controls → "Security events delivered"; the evidence bundle's `security_events`; `docs/guides/siem.md` |
| Continuous monitoring | Live controls with PASS / WARN / FAIL and evidence, by API for a bank's monitoring tools. `openclear controls` exits non-zero on a failure. | OC-CA-01: CA-7<br>OC-CA-02: CA-7, SI-4 | Settings → Controls |
| Timeliness | Exceptions past or near their decision deadline, stuck imports, a late decision outbox. | OC-SI-05: SI-4, CP-2<br>OC-SI-06: CA-3 | Controls → "Decisions and imports on time" |

## Development, change and resilience

| Theme | What OpenClear does | Controls (NIST SP 800-53) | Evidence |
|---|---|---|---|
| Secure development | An automated suite of about 1,400 tests on SQLite, Postgres and Linux before each release. A published API contract that changes only additively. A written security review with fixes. | OC-CM-02: CM-3, SI-2, CP-10 | `SECURITY_REVIEW.md`; `CHANGELOG.md`; CI |
| Change control in production | Rules and models in force, with a SHA-256 of each, in every evidence export. Two-person approval for rule and model changes. Database upgrades and downgrades are versioned. | OC-AC-07: CM-3, CM-5, AC-3(2)<br>OC-SI-04: CM-4, CA-2<br>OC-CM-03: CM-2, CM-8 | The evidence bundle's `rules` and `models` |
| Input handling | A corpus of malformed X9.37 and NACHA files run through the real parsers, which must refuse each cleanly and quickly. | OC-SI-01: SI-10<br>OC-SI-02: SI-10, SI-7 | Controls → "Malformed files refused" |
| Production safety | Refuses to start in production with development keys or unsafe options. | OC-CM-01: CM-6 | Controls → "Production settings" |
| Exit | A full export of a bank's data, for migration or offboarding. | — | Settings → Data export |
