# Inputs, limits and outside connections

What OpenClear accepts, how it checks it, what limits protect it, and every outside system it
talks to. It answers ASVS 5.0 2.1.1–2.1.3, 5.1.1, 13.1.1, 13.2.4, 14.1.1, 14.1.2, 15.1.1 and
15.1.3. How sensitive each kind of data is, and the protection each level needs, is in the
[risk profile](risk-profile.md).

## Input validation

Everything is checked on the server: the browser's checks are only a convenience.

| Input | Checked by | Rules |
|---|---|---|
| API requests | Pydantic schemas per endpoint (`schemas/`) | Types, required fields, lengths, enumerations; unknown fields can't set anything (ASVS 15.3.3). Invalid requests get `422` with the fields at fault. |
| Web forms | The route's typed form fields and the service it calls | As the API, plus a CSRF token on every change |
| Issued-check files: CSV, fixed width, Excel | `bulk_import/tabular.py`, `layouts.py`, `fields.py` | Each row on its own: an account that exists and is in scope, and a valid check number, amount, payee and issue date. A bad row is reported with its reason and the good rows still load (OC-SI-02). |
| X9.37 image cash letters | `bulk_import/x937.py` | Record structure and lengths, EBCDIC or ASCII, images read one check at a time |
| NACHA ACH files | `bulk_import/nacha.py`, `networks/ach/` | Record types and lengths, batch and file control totals |
| Zip archives of images | `bulk_import/zip_import.py` | Declared and actual uncompressed size, names stripped of directories |
| Images (TIFF, JPEG, PNG) | `bulk_import/images.py` | Decoded with Pillow's decompression-bomb limit, then re-encoded to PNG; anything else refused |
| Markdown (banners, messages) | `web/templates.py` | Rendered, then sanitized with nh3 to an allowlist of tags |
| URLs for webhooks, SIEM and SSO | `auth/outbound_http.py` | `https`, no credentials, and only public addresses, checked again on each connection |

### Business rules and limits

- **Sequence:** a decision applies only to an open exception before its return deadline; a
  decided exception can't be decided again; dual control and segregation of duties hold
  whatever the order of requests (`services/`, OC-AC-05, OC-AC-06).
- **Per person and system:** 600 requests a minute; per IP address, 120 a minute; token
  requests and one-time links, 30 a minute ([authentication](authentication.md)).
- **Per request:** 50 MB (`max_request_body_bytes`); a zip may expand to 200 MB
  (`max_zip_uncompressed_bytes`).

## Files

| Kind | Types | Largest |
|---|---|---|
| Issued checks, people, accounts, ACH | CSV, fixed width, Excel (`.xlsx`) | 50 MB a request |
| Presented checks with images | X9.37 image cash letters, or a CSV with a zip of images | 50 MB uploaded, 200 MB unzipped |
| Check images | TIFF (one or two pages), JPEG, PNG | 50 MB; stored as PNG |
| ACH | NACHA | 50 MB |
| Branding | PNG, JPEG, ICO (no SVG) | 50 MB a request |

Uploaded files are stored under generated names, never served back except to people of the bank
that uploaded them, and kept with their SHA-256 and a signature (OC-SC-03). Scanning them for
malware is the bank's or service provider's (a CUEC), with ClamAV and VirusTotal hooks planned
for 1.25 (OWASP_PLAN.md OW-D34).

## Outside connections

OpenClear connects only to these, and only to public addresses; each is off until configured,
and each bank reviews the ones it uses (Settings → Outside services).

| System | Why | Set by |
|---|---|---|
| The bank's identity provider(s) (OIDC discovery, token, JWKS; SAML metadata is uploaded) | Single sign-on | Bank |
| The bank's SIEM (Splunk HEC, syslog over TLS) | Security events | Bank or service provider |
| Webhook receivers | Decisions and events to the bank's systems | Bank |
| Email (SMTP) and SMS (Twilio) providers | Notifications | Service provider |
| Cloud OCR (Amazon Textract, Azure Document Intelligence) | Reading check images, if chosen over the built-in Tesseract | Service provider |
| Object storage (S3-compatible, Azure Blob) | Files, if chosen over local disk | Service provider |
| Redis | Shared rate-limit counters across instances | Service provider |
| GitHub's releases API | Checking for a newer OpenClear (no data sent) | Service provider (`release_feed_url`) |
| Have I Been Pwned's range API | Breached-password check, if turned on (only 5 hash characters sent) | Service provider |

Inbound, OpenClear is called by browsers, the bank's systems and customers' systems through the
API, the bank's directory (SCIM), identity providers (SAML posts, back-channel logout), and
online banking (embedded sessions).

## Keeping dependencies current

A known vulnerability in a dependency with a fix available fails the build (`pip-audit`), unless
waived in `.pip-audit-waivers.toml` with a reason and an expiry. Fixed releases follow the time
frames in [SECURITY.md](../../SECURITY.md): 7 days for critical, 30 for high, 90 for medium, the
next release for low. Dependabot and a weekly lock refresh propose updates; every release
publishes an SBOM.

## Work that takes time

These are bounded so one request can't exhaust the server:

| Work | How it's bounded |
|---|---|
| Large uploads (X9.37, zips) | Request size limit; images read one at a time; processed as background jobs |
| Data exports | A background job with a time limit (`data_export_timeout_seconds`, default 3600) |
| Replay of a historical file through new rules | A background job (OC-SI-04) |
| Action-log verification and archiving | Background jobs, on a schedule |
| OCR of check images | Background jobs; Tesseract has a per-image time limit |
| Model training | A background job, only by the service provider's operators |
| Password hashing | bcrypt's cost, behind the sign-in rate limits |
