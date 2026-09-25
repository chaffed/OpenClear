# Security Review: Confidentiality, Integrity, Availability

Date: 2026-07-28
Scope: all features shipped to date (multi-tenant/customer core, auth + federated SSO +
WebAuthn, users/security groups/Bookkeeper access, accounts, issued items/stop
payments/paid items/check images, ACH, exceptions/decisions, ML scoring, bulk import,
data export, audit log, background workers).

This is an evaluation, not a fix list — findings are ordered most-severe-first within
each section, with file references so each can be independently verified. A "what's
solid" note closes out each section; those are deliberate design choices worth keeping,
not gaps.

## Confidentiality

**FIXED — hardcoded secrets silently used as production defaults.**
The three signing secrets (`jwt_secret_key`, `file_signing_secret`,
`audit_log_signing_secret`) are now ECDSA P-256 key pairs, generated per-deployment via
`scripts/generate_keys.py` (see README.md's "Signing keys" section), not shared secret
strings — the checked-in `dev_keys/` pair is deliberately public and only usable
locally. `config.py::assert_production_safe`, called at the top of
`main.py::create_app()`, now refuses to start the app at all if
`OPENCLEAR_ENVIRONMENT=production` and any of the three key pairs (or `sso_encryption_key`,
which stays a symmetric secret since it's encryption, not signing) are still at their
checked-in/default values — verified by hand: the app raises `RuntimeError` and exits
immediately rather than serving requests insecurely.

**FIXED — check images aren't customer-scoped.**
`CheckImage` and `BulkUploadFile` now both have a `customer_id` column
(`CustomerScopedRepository`, same pattern as accounts/issued items/paid items/ACH), set
from the resolved account/paid item at creation time (check images) or the uploader's
own scope (bulk upload files). Fixed across both the web UI and the JSON API — the API's
`check-images` upload route had the same gap plus a pre-existing bug (`paid_item_id` was
declared as a bare query param even though the route only accepts multipart form data,
so it was silently ignored; now `Form(None)`). Verified end-to-end: a customer-scoped
user gets 404 on every check-image and bulk-upload-file route for another customer's
records; a customer-scoped data export now correctly includes its own check images
(including ones not yet linked to a paid item, which the old join-through-paid-item
workaround missed) and never another customer's.

~~High — bulk-uploaded source files have the identical gap.~~ — **done**, see above.

**High — the "global" ML model pools every tenant's decision data.**
No `tenant_id` filter anywhere in `ml/train.py`, `ml/registry.py`, or `workers/tasks.py`;
`MlModel` has no `tenant_id` column at all. Worse, raw `tenant_id` is embedded as a
literal training feature (`networks/check/features.py:49`, `networks/ach/features.py:20`)
and surfaces via `feature_importance()` — a coefficient dump reveals which tenants exist
and how they correlate with pay/return outcomes. This was a deliberate architecture
choice, not an oversight — `api/v1/admin.py:21-26` documents it as intentional (solving
the cold-start problem for a brand-new tenant with too little of its own decision
history) and explicitly flags it as needing a real fix — restricting these endpoints to
a platform-level role, not per-tenant `admin:manage` — before real multi-tenant
production use. It's an open IOU, not a hidden bug, but it's still open.

**FIXED — no login rate limiting or account lockout anywhere.**
`User` (`domain/user.py`) now carries `failed_login_attempts`/`locked_until`.
`auth/login_service.py::authenticate_password` increments the counter on each wrong
password for a known, active account and locks it (`login_max_failed_attempts` = 5,
`login_lockout_minutes` = 15, both configurable in `config.py`) once the threshold is
reached, returning a new `LOCKED` outcome that both the web login form and the JSON API
(`423 Locked`) surface distinctly from an ordinary wrong-password rejection — including
rejecting the *correct* password while locked, so an attacker's Nth guess landing can't
bypass the lock. The counter resets to zero the moment a password verifies correctly,
regardless of what happens afterward (e.g. an SSO-required scope). Throttling is
account-only (by email), not IP-based, as a brute force targets the password regardless
of source address. An admin can also manually clear a lockout early
(`services/user_service.py::unlock_user`, exposed as an "Unlock" button on the Users
page) rather than only waiting out the auto-expiry. Verified with unit tests
(`tests/test_auth/test_login_service.py`), web/API integration tests asserting the 401
lockout message and 423 status respectively, and an end-to-end smoke test (5 failed
attempts → lock → correct password still rejected → admin unlock → login succeeds again).

**FIXED — no bank-configurable session timeout.**
`Tenant` gained nullable `access_token_expire_minutes`/`refresh_token_expire_minutes`
columns (`None` = use the platform default from `config.py`). Every one of `create_token`'s
8 call sites across web and API login/refresh/switch-tenant/WebAuthn flows now threads the
resolved tenant through to `auth/security.py::create_token`, which honors the override for
the matching token type (the short-lived `mfa_pending` token is deliberately exempt — a
bank lengthening session lifetime shouldn't also lengthen how long a WebAuthn ceremony has
to complete). A new "Session timeout" form on the organization settings page
(`/ui/settings/session-timeout`) lets a tenant admin set or clear both values; blank means
"use the platform default." Verified with a unit test on `create_token`'s override
behavior and a web integration test confirming the issued access token's `exp` claim
reflects the configured value end-to-end.

**FIXED — CSV formula injection.**
A broader audit (two parallel investigations plus direct verification) found the actual
surface was much narrower than the original wording implied: OCR is a library call, never
a shell string (no command-injection path); every OCR-derived field only ever reaches
JSON, never CSV; and exactly one CSV writer exists in the whole app —
`/ui/users/export.csv`. That one now runs every cell through `_csv_safe()`, prefixing a
literal `'` on any value starting with `=`/`+`/`-`/`@` (the standard Excel/Sheets
"force text" mitigation) — `export_users_json`'s data is deliberately untouched, since
JSON isn't opened in a spreadsheet. Two related findings surfaced during the same audit
and fixed alongside it:
- `bulk_import/file_storage.py::save_uploaded_file` embedded the raw client-supplied
  filename in the on-disk path with no sanitization — reproduced directly that a
  traversal-shaped filename didn't currently escape the storage directory (pathlib's
  `write_bytes` doesn't auto-create missing parent directories, so it just crashed with
  an unhandled 500), but that was accidental, not a real control. Now sanitized with
  `Path(filename).name`.
- `bulk_import/images.py::normalize_and_split_image` didn't catch
  `Image.DecompressionBombError` — Pillow's own default decompression-bomb guard was
  already active (never disabled anywhere in this repo) but surfaced as a raw exception
  instead of this module's clean per-row error.

**FIXED — data export's secret exclusion was fragile.**
The generic row exporter (`_row_to_dict`) correctly strips `hashed_password`/
`client_secret_encrypted` for every exporter that uses it. Re-verified while fixing this:
`_sso_group_mapping_dicts` was actually already safe — it does call `_row_to_dict` per
row — so the original wording naming it alongside `_users_dicts` was inaccurate; only
`_users_dicts` hand-composes its dict without going through the shared filter. It's now
run through the same exclusion set as a mechanical safety net (it didn't emit either
secret field before this either, but nothing structurally prevented a future edit from
reintroducing one). More importantly, a new test walks a fully-populated tenant-wide
export's entire archive — every entity, not just users/SSO — and asserts no
`_SECRET_COLUMNS` name appears anywhere, as a tripwire against any *future* exporter
leaking a secret column, which is the real fix for "correct only because someone
remembered by hand."

**What's genuinely solid:** permissions are re-resolved from the DB on every request
rather than baked into the JWT, so revoking a membership or editing a group takes effect
immediately; passwords are bcrypt-hashed; SSO client secrets are Fernet-encrypted at rest
under a key separate from the JWT signing key; cookies are httponly/secure/samesite with
narrow path scoping for refresh and MFA cookies; CSRF uses a constant-time double-submit
check; `next` redirects are validated against open-redirect; and the Bookkeeper/
cross-tenant tooling was deliberately built to never leak one tenant's access into
another admin's view.

## Integrity

**Accepted — dual control defaults to off.**
`Tenant.require_dual_control` defaults to `False` for every new tenant, so unless a bank
explicitly opts in, one person holding both `exception:recommend` and `exception:decide`
(Admin, or the new Bookkeeper preset) can unilaterally finalize a pay/return decision with
no second reviewer. Reviewed and accepted as the correct default, not a gap: dual control
isn't the common case for positive pay, though some banks do request it — which is exactly
why it's a per-tenant opt-in (`/ui/settings` → "Require dual control") rather than mandatory.

**FIXED — `activate_model()` has no ownership check.**
`activate_model` now takes a required, keyword-only `expected_customer_id` and rejects
(with the same "not found" message as an unknown model_id, so it doesn't reveal that a
mismatched model exists) any model whose own `customer_id` doesn't match — closing the
web bank-wide route, the web per-customer route, the API route, and `train_model`'s own
internal promotion call. Verified: a customer-scoped model can no longer be activated via
the bank-wide route or a different customer's page, only via its own.

**FIXED — NACHA/X9.37 control totals are not validated.**
`nacha.py` now cross-checks batch control (type 8) and file control (type 9) records'
declared entry/addenda count, entry hash, and total debit/credit dollar amounts against
what was actually parsed, raising a clear per-field mismatch error. `x937.py` does the
same for Cash Letter Control (type 90) and File Control (type 99) item count and total
dollar amount, and (since Phase 8 of FIX_PLAN.md) Bundle Control (type 70) against the
checks since the preceding Bundle Header (type 20). X9.37's control-record field positions are
this module's own best-effort, documented convention (same caveat its test suite already
disclosed for the rest of the parser) — there's no real sample cash-letter file in this
repo to verify byte-for-byte against, so if a real file from your processor gets rejected
on a mismatch that looks wrong, check those positions first.

**Low — FIXED in 1.5.0 (see the end of this document) — `joblib.load` (pickle) deserializes model artifacts with no signature check** —
not attacker-reachable today since the path comes from the DB, but no defense-in-depth if
that ever changes.

**What's genuinely solid:** the audit log is a real hash chain (ECDSA P-256 signature over
canonical fields including `prev_entry_hash`, since the key-pair signing change above),
with a `verify_chain` endpoint that detects tampering; there's no delete/purge path for
audit rows at the app layer; bulk-upload reversal is idempotent and checks current record
state before voiding (won't undo something already paid); WebAuthn has clone-detection
via sign-count regression.

## Availability

**FIXED — no request/file size limits anywhere.**
A new middleware (`main.py`) rejects any request whose `Content-Length` exceeds
`max_request_body_bytes` (default 50MB) with a 413, before any route or parser ever sees
the body — covers tabular/NACHA/X937 uploads uniformly with one change. Doesn't catch a
chunked-transfer-encoded request that omits `Content-Length`; not adding a full streaming
body-size enforcer for that, a deliberate proportionality call. `zip_import.py` separately
sums every entry's own declared uncompressed size before reading any of them, rejecting
if the total exceeds `max_zip_uncompressed_bytes` (default 200MB) — the zip-bomb-specific
case the request-size check structurally can't catch. OCR calls now also pass a
30-second timeout (`ocr_timeout_seconds`), closing off a pathological image hanging a
background-task slot indefinitely.

**FIXED — ML retrain endpoints have no rate limit or lock.**
`train_model` now rejects a retrain of the same `(network_code, customer_id)` pair within
`ml_retrain_cooldown_seconds` (default 60) of the last one, checked before loading any
data — one choke point covering the web routes, the API route, and the background job
alike, closing the on-demand-hammering vector.

**FIXED — the background retrain job has no persistent queue.**
Kept as a deliberate scope decision (a real job queue is a separate, bigger
infrastructure question), but `retrain_job()`'s two loops now go through a shared
`_train_and_log` helper that catches any unexpected exception (not just the
already-handled "not enough data yet" case), rolls back cleanly, records a `FAILED`
`MlModel` row (visible on the admin models page — `MlModelStatus.FAILED` existed in the
enum but was never actually written before this), and moves on to the next
network/customer instead of aborting the rest of the run.

**FIXED — data export archives had no row limit, pagination, or timeout.**
`_build_archive` (`services/data_export_service.py`) now checks a time budget
(`config.py::data_export_timeout_seconds`, default 5 minutes, overridable per-tenant via
a new "Data export timeout" field on `/ui/settings`) before starting each named entity's
export and before each check-image/bulk-upload-file copy, failing the job clearly
(`ExportTimedOut`) rather than letting it hang indefinitely. Separately, any single
entity whose row count exceeds `data_export_max_rows_per_entity` (default 50,000, a
global technical ceiling, not a per-tenant policy value) fails the job with a clear
message (`ExportRowLimitExceeded`) before its JSON is ever written into the archive.
This is a deliberately proportional fix, not a full streaming/paginated rewrite of every
repository: the check is cooperative, between units of work, so a single entity whose own
query is itself slow isn't interrupted mid-query — same "sum sizes before reading" spirit
as `bulk_import/zip_import.py`'s existing zip-bomb guard, matched to this finding's
Low/Low-Medium severity. Both new exceptions are caught by `run_export_job`'s existing
broad exception handler, which already marks the job `FAILED` with a clear message — no
new failure-handling path was needed.

**What's genuinely solid:** every bulk-import path commits per-row, so one bad row can't
abort or corrupt the rest of a file; OCR and export background tasks are wrapped in broad
exception handlers that mark the job FAILED rather than hang.

## If you want to fix three things first

1. ~~Fail loudly on startup if any of the four secrets are still at their dev default
   outside a local/test environment~~ — **done**: key-pair signing + `assert_production_safe`.
2. **Add `customer_id` scoping to check images and bulk-upload-file downloads** —
   straightforward, same pattern already used everywhere else, closes a real disclosure
   that requires no misconfiguration to exploit.
3. **Tenant-scope the ML training/activation pipeline** (or explicitly restrict it to a
   platform-level role, as the existing code comment suggests) — already flagged by the
   code itself, just not yet acted on.


## Update: 2026-09-22 (see EVALUATION.md / FIX_PLAN.md)

**FIXED — customer-scoped users could export the whole bank or other customers.**
`data_export:run` is deliberately not masked for customer-scoped sessions (customers may
export their own data), but `web/routers/data_export.py` never checked
`ctx.customer_id`. Bank-wide export routes now 403 for customer-scoped sessions, and
per-customer routes 404 unless the path customer is the session's own. Regression tests in
`tests/test_web/test_web_data_export.py`.

**FIXED — demoting a user didn't take effect until their token expired.**
`auth/deps.py::decode_and_build_context` resolved permissions from the token's
`security_group_id` claim. It now uses the membership's current group (and requires it to
belong to the token's tenant), so a group reassignment applies on the next request, web and
API alike. Regression tests in `tests/test_web/test_web_users.py`.

**FIXED — unhandled errors surfaced as bare 500s.** App-wide handlers now render a branded
error page (or JSON under `/api`) without exception details, and uniqueness conflicts
become a 409. The new-account form validates `customer_id` against the tenant's own
customers (previously any UUID was accepted).

**FIXED — no way to change or reset a password.** Users can now change their own
password (current password required; a wrong one counts toward lockout; the new one must
meet the strictest policy across all their memberships), and admins can issue a one-time
temporary password that forces a change at next sign-in (other pages redirect, API
refused). Resets are refused for users who belong to other organizations, for the admin's
own account, and in the demo organization. Every change is audit-logged, and the user
always gets an email about it.

**FIXED — tokens couldn't be revoked, and web sessions couldn't renew.** Logout now revokes
that login session server-side (`revoked_session`, keyed by a per-login `sid` claim);
password changes/resets and an admin "Sign out everywhere" bump `User.token_version`,
killing every token the user holds. The web UI renews sessions while the user is active,
with the idle timeout enforced server-side and renewals capped at the maximum session
length. Signed-in responses are now `Cache-Control: no-store`. Also fixed: the API
WebAuthn sign-in dropped `customer_id` from its tokens.

**FIXED (2026-09-23) — SSRF through SSO issuer URLs.** Issuers must be public https URLs
(checked on save), and every outbound OIDC request (discovery, JWKS, token exchange, and each
endpoint the discovery document names) goes through `auth/outbound_http.py`. It re-resolves the
host at connect time, refuses non-public addresses, and connects to the vetted IP, so DNS
rebinding doesn't get around it. There's an opt-in for local test IdPs, refused in production.
The OAuth `state` is now bound and verified.

**FIXED (2026-09-23) — uploaded content served from the app origin.** Logos/favicons are typed
from their bytes (PNG/JPEG/ICO only, no SVG) and served with a sandboxing CSP. Bulk-upload
downloads are always `application/octet-stream`.

**FIXED (2026-09-23) — the public demo had no guardrails.** A central lock list
(`web/demo_guard.py`, web and API) refuses actions that would let one visitor lock out or
disrupt the others. The demo resets hourly, and resets now also restore the organization's
own settings.

**FIXED (2026-09-23) — any tenant's admin controlled the global ML model that scores every
tenant** (the long-standing IOU in the original review above). Each organization now chooses
the shared model or a bank-only model. The shared model is trained only on organizations that
chose it, and can be retrained, activated, or inspected only with a platform API key carrying the
new `shared_model` scope (existing keys are usage-only). Bank admins manage only their own
bank-only model, and model activation is ownership-checked by slot. Other banks'
fraud-training examples need platform approval before they train the shared model (anti-poisoning).
The raw `tenant_id` model feature is gone, so the shared model's coefficients no longer reveal
which organizations exist. When a bank leaves, the shared model is rebuilt without its data.
Also fixed: the admin model-list API returned every bank's models, and the admin page's
training counts covered all banks.

**FIXED 2026-09-23 (FIX_PLAN Phase 9, release 1.5.0):** the last open item from the
original review, model artifacts loaded with `joblib` (pickle) without a signature check.
Each file's SHA-256 is now recorded on its `ml_model` row when it's written
(`artifact_sha256`). `ml/registry.py::ArtifactStore.load_model` hashes the file and
unpickles those same in-memory bytes only if they match, and refuses a row with no
recorded hash. The database was already the trust root (it chooses which file to load), so
this needs no new key. Someone who can write to the artifact directory or a shared volume,
but not the database, can no longer get code run. A failed check leaves exceptions
unscored and is logged. It never blocks ingestion. The upgrade migration pins existing
files.
