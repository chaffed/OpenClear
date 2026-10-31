# Review plan: what 1.28 to 1.33 added

A review of OpenClear 1.33.0 (`0bafc661`, 2026-10-31) covering what changed since 1.27.0:
teller verification, reverse positive pay and posted-check layouts (1.28), the regulatory plan
(1.29), offboarding a bank and a customer (1.30), custom fields (1.31), the SAML re-key (1.32),
and the third-party inventory, housekeeping and build changes (1.33). 104 commits, about 7,500
lines of source. Each cluster was read end to end by a reviewer with the same brief (the five
axes below, the severity scale, "trace it or mark it PLAUSIBLE"), and every High and Medium was
then checked again against the code by hand. The 1.26.0 review (FULL_REVIEW_PLAN.md) is not
repeated: its locks, claims and leases were spot-checked where the new code touches them, and
hold.

**The short version.** The new code uses the 1.27 patterns where it should: the teller
endpoint takes the issued-item lock, `cashed` claims its verification once, the cut-off sweep and
*Pay all* go through `get_for_update`, every new table has its RLS policy, every new route its
permission and CSRF, and the deletion code enumerates tables from the metadata so nothing added
later is missed. The weaknesses are in what the code *doesn't* do rather than in races. A check
exception decided *pay* never marks the paid item or the issued item paid, so on a reverse
positive pay account the same check can be paid twice by silence. Bank deletion wipes every row
but not every storage area: the decision files the core received stay on disk. A core's routine
customer sync reactivates an offboarded customer while its deletion stays scheduled. The second
tier of findings is about promises the code keeps only on paper: "two operators" degrades to one
operator plus 24 hours when there's no second one; "a check is never due after midnight" means a
late-posted file's exceptions are defaulted at the next sweep with no review; the teller
permission that "can't download a check register" answers each probe with the check's amount and
payee; the third-party page's license texts are never compared against a regeneration in any
test that runs.

Finding ids keep the reviewer's prefix: **T** teller, reverse and layouts · **G** regulatory ·
**O** offboarding · **C** custom fields · **K** SAML, third-party, build.

## Findings

Severity: **High** = money or compliance impact with no mitigation, or a real security
weakening; **Medium** = wrong behaviour in a reachable case, or a hardening a bank's reviewer
would ask for; **Low** = polish, or needs an unlikely precondition. **CONFIRMED** = the path was
traced end to end (and, where marked, reproduced against the app); **PLAUSIBLE** = the problem
is there but its reach wasn't fully traced.

### A. Money path and data integrity

| # | Sev | Finding | Where | Status |
|---|---|---|---|---|
| T1 | High | **A check paid after a decision is never marked paid, so reverse positive pay pays the same check twice.** `settlement_status = PAID` and `issued_item.status = PAID` are set only on a clean rules match; no decision path (customer, bulk, *Pay all*, the default at the cut-off) sets either. On a reverse account every check is an exception, so after one is paid at the cut-off the paid item stays `pending` and a second presentment of the same account, number and amount finds no `duplicate_paid` (that rule needs `PAID`) and no paid candidate: it's "To review" again, and paid again by silence. The CHANGELOG and RPP-D2 say the opposite. The same gap reaches ordinary accounts: an amount mismatch the customer accepts leaves the issued item unpaid, so FR-D11's "a paid candidate is a duplicate at any amount" never fires for it. Reproduced. | `networks/check/ingestion.py:66-104`; `networks/check/adapter.py:105-115`; `services/auto_disposition_service.py:135-221`; `services/decision_service.py` (no settlement write for checks) | CONFIRMED |
| O1 | High | **Decision files and ML artifacts survive bank deletion.** `FILE_AREAS` is a hand-written tuple of five areas; `storage.AREAS` has seven. The outbox writes the bank's decision files (account and check numbers, amounts, pay or return) under `decision_files/<tenant_id>/`, which `_delete_files` never touches; the `decision_batch` rows go, so the files are unreferenced but present on disk, S3 or Azure. `ml_artifacts` is cleared only by the paths on surviving `MlModel` rows. The page promises "Every file", and the certificate's `storage_areas_cleared` lists the five. | `services/offboarding_service.py:36, 261-269, 384`; `storage.py:44-52`; `services/decision_outbox_service.py:148` | CONFIRMED |
| O2 | High | **One `PUT /api/v1/customers/{number}` reactivates an ended customer and leaves it scheduled for deletion.** `upsert_customer` sets `customer.is_active = payload.is_active` unconditionally; `end_service` only set `is_active=False` and `service_ended_at`. A core's routine sync with `is_active: true` (realistic for a customer that stops positive pay and stays a customer, CO-D1) lets its people sign in again (the gate checks `is_active` only; memberships were never deactivated) and tellers see it as enrolled, while `_refuse_ended` still refuses its files and the sweep still deletes everything 90 days after `service_ended_at`. It bypasses the two-admin restore. | `api/v1/customers.py:113`; `services/customer_offboarding_service.py:179-189, 342-349`; `auth/deps.py:165-168`; `services/teller_service.py:124-132` | CONFIRMED |
| G1 | Medium | **A late-arriving check is defaulted at once, with no review window.** `within_return_deadline` caps the decision deadline at the return day's cut-off; when the file reaches OpenClear after that (a core posting yesterday's Business Date mid-morning, an API backlog) the deadline is already past, and the disposition sweep (every 300 s) applies FIXED_PAY or FIXED_RETURN before anyone sees the item. `after_return_deadline` stays false (locked before midnight), `deadline_approaching` never fires, the minimum review time is zero. A fraudulent check on a FIXED_PAY account is paid unreviewed although the bank had until midnight. The docstring says "due at once": a design choice whose consequence wasn't drawn. | `services/cutoff_service.py:209-228`; `networks/check/ingestion.py:76-93`; `services/auto_disposition_service.py:135-183`; `workers/tasks.py:126-215` | CONFIRMED |
| T5 | Medium | **Two branches verifying the same check at once both get `match`.** A verification changes no state, so the issued-item lock serialises verify against cash or presentment only; two verifies within the 15-minute window both answer `match`, both tellers pay out, and only the second `cashed` learns `duplicate_paid` after the cash is gone. The guide's "Two branches at once … can't both be paid" overstates what the lock does. Reproduced. | `services/teller_service.py:98-121, 140-157, 202-243`; `docs/guides/teller.md` | CONFIRMED |
| O7 | Medium | **No freeze between approval and the wipe.** `run` sets the bank inactive only in `_strip`, at the end; during the 24-hour delay and the run itself, sign-ins, API clients, imports and the bank's own jobs carry on. `_wipe` deletes the bank's `job` rows, a running import's among them, while that worker's later inserts commit afterwards and aren't in `rows_deleted`. | `services/offboarding_service.py:341-403`; `workers/jobs.py:200-250` | PLAUSIBLE |
| O6 | Medium | **Kept people keep their personal rows.** `_forget` deletes the `_PERSONAL` rows and the user in one savepoint; when the user must stay (for a customer deletion, anyone named in a kept `audit_log_entry`, which is anyone who ever acted) the `IntegrityError` rolls the personal deletes back too: `login_session`, `webauthn_credential`, `password_reset`, `user_device`, `notification` (bodies about the customer's items), `notification_preference`, `idempotency_record`, `revoked_session`, and the user row with its password hash. `_collect` never reaches them (no FK path from `customer`). The certificate counts them as "kept". | `services/offboarding_service.py:275-309`; `services/customer_offboarding_service.py:201-222, 282-294` | CONFIRMED |
| O8 | Medium | **Customer deletion removes the bank's own records.** `_collect` reaches `billing_statement` and its lines (finalised invoices), `cuec_attestation`, `access_review` and its items, `teller_verification`, customer-scoped `change_request` and `custom_field_value`; only `audit_log_entry` and `security_event` are kept. A bank's auditor expects finalised billing and attestation evidence to outlive a client. | `services/customer_offboarding_service.py:35, 201-222` | CONFIRMED |
| C1 | Medium | **Negative numbers in `cf_` columns are corrupted in every CSV decision output.** `_csv_safe` prefixes `'` to any string starting with `-`; `to_csv` applies it to every custom-field column. `parse` accepts signed integers and decimals, so `-5` reaches the core's file (feed.csv, `GET /decisions/file`, the outbox's OpenClear CSV) as `'-5`. The JSON feed is right. | `services/decision_feed_service.py:146-167`; `plugins/builtin.py:90-98`; `services/decision_outbox_service.py:136-140` | CONFIRMED |
| G2 | Medium | **The OpenClear CSV decision file gained a trailing column unannounced.** `CSV_COLUMNS` now ends with `after_return_deadline`; that layout is the outbox file cores import. The CHANGELOG names the feed and the webhook only, and no page lists the layout's columns, so a core that validates the count or maps by position stops on the first file after the upgrade. | `services/decision_feed_service.py:139-156`; `plugins/builtin.py:94-100` | CONFIRMED |
| T11 | Low | **Pay-all's "list changed" guard is count-only.** One item decided plus one newly arrived leaves `len(items) == expected`, so a check the person never saw is paid. Bounded: each decision takes the item lock, and a review-only check is paid at the cut-off anyway. | `web/routers/exceptions.py:527-556` | CONFIRMED |
| T10 | Low | **`cashed` isn't reconcilable on retry.** A platform that lost the response gets 409 "already recorded as cashed" with no `paid_item_id`, so it can't tell its own success from a double-cash by someone else. | `services/teller_service.py:218-225`; `api/v1/checks.py:84-97` | CONFIRMED |
| G9 | Low | **Items open at the upgrade have no return deadline**, so a late decision on one is never flagged. Documented in the migration; one-off. | `migrations/versions/44ed7f9be66a_…:6-8` | CONFIRMED |

### B. Security

| # | Sev | Finding | Where | Status |
|---|---|---|---|---|
| O3 | Medium | **"Two operators" becomes "one operator plus 24 hours" when nobody else can approve.** `request_or_apply` sets `applies_at = now + COOLING_OFF` whenever `approvers(..., excluding=user_id)` is empty, for every tier including `protection`, and `settle()` applies it. A sole operator can start offboarding, wait a day, request deletion, wait a day, and the wipe runs a day later with no second person; the certificate shows one email as requester and approver. The same holds for `customer.offboard` at a bank with one admin. | `services/change_request_service.py:119-132, 191-197, 310-345`; `services/change_kinds.py:1531-1560` | CONFIRMED |
| T2 | Medium | **`payee_needed` hands back the payee it asks for.** When the issued check names a payee and the platform sends none, the route still returns `issued` (amount, payee, issue date), so the platform can resend with the payee it was just given and get `match`. The owner's rule (the payee as written on the check) is bypassable by the integration, not enforced. Reproduced. | `api/v1/checks.py:69-81`; `services/teller_service.py:135-157` | CONFIRMED |
| T3 | Medium | **`check:verify` is a check-register oracle the probing detector doesn't watch.** Iterating check numbers on a known account with `amount=0.01` returns `amount_differs` plus `issued` for every issued check; `_watch_for_probing` counts only `not_issued` and `not_on_positive_pay`, and the route has no `rate_limit()` of its own. TL-D2 promised the permission "can't be used to download a customer's check register". Each call is audited and raises `teller.verify.declined` (medium), but the high alert built for this never fires. Reproduced. | `services/teller_service.py:177-199`; `api/v1/checks.py:69-71` | CONFIRMED |
| K2 | Medium | **The CodeQL #176 pattern survives across `web/routers/`.** HK1 fixed one site; `except ValueError as exc` → `error=str(exc)` (117 `str(exc)` sites in the routers), and `tenant_settings.py:930` catches bare `Exception` and shows `TypeName: message`. Any deeper `ValueError` (uuid, int, fromisoformat, a library) shows its text, and CodeQL will raise the same class of alert on the next path it scans. | e.g. `sso_settings.py:170-640`; `people.py:108-455`; `customers.py:312-380`; `users.py:317, 837`; `tenant_settings.py:930-932, 982` | CONFIRMED |
| K3 | Medium | **Two SAML re-key states break sign-in and the page doesn't say so.** `finish` nulls the old key irreversibly; if the identity provider hasn't taken the new certificate, every sign-in fails until it's reconfigured, with no undo. `cancel` after the provider already switched also breaks sign-in until the old certificate is re-given. The page describes the happy path only. | `templates/sso/form.html:127-142`; `services/sso_service.py:191-203` | CONFIRMED |
| C2 | Medium | **The Redis limiter fails open silently for as long as Redis is down.** `RedisRateLimiter.allow` returns True on any exception (one log line a minute); `allow()` never falls back to the in-process limiter in the same module; nothing in health, the control checks or the security events reports it. The 1.31.0 fix (one 0.5 s try) rightly removed the stalls, and made this the steady state of an outage, for sign-in, OAuth tokens and password reset. | `web/rate_limit.py:109-150`; callers in `api/v1/auth.py`, `api/v1/oauth.py`, `web/routers/auth.py`, `password_reset.py` | CONFIRMED |
| T6 | Low | **The probing detector is evadable by a person's token.** `api_client_id` is looked up by user; a person granted `check:verify` has none, so only the caller-supplied `teller_id` and `branch` are counted and rotating them defeats the threshold. Mitigated: `check:verify` is outside Admin's default grant. | `services/teller_service.py:90-95, 182-199` | CONFIRMED |
| C3 | Low | **A customer-scoped feed reader can learn the bank's hidden field keys.** `output_keys` validates against all fields: a hidden key returns 200 with null values and a `cf_` header, an unknown one 422. Values never leak, only a key's existence. | `services/custom_field_service.py:366-379`; `api/v1/decision_feed.py:33-39` | CONFIRMED |
| K4 | Low | **Bank staff re-key a customer's connection with no dual control**, under `customer:manage`, even for a `managed_by="customer"` connection whose own rule would need a second approver; consistent with the other bank edits, but `finish` is the irreversible step. | `web/routers/sso_settings.py:574-593` | CONFIRMED |
| G8 | Low | **The yearly contact confirmation isn't a security event**: `incident_contact.confirmed` is audited but unmapped, unlike `incident_contact.changed`. | `services/incident_service.py:98`; `services/security_events.py` | CONFIRMED |
| K9 | Low | **`postgres:16` by tag** in the Postgres shard's service (everything else is pinned); `_waiver` accepts an alert with no instances under a URL-scoped waiver (`all([])`). | `.github/workflows/tests.yml:82`; `scripts/zap_scan.py:91` | CONFIRMED |

**Checked and solid.** Every new table has `ENABLE` and `FORCE ROW LEVEL SECURITY` with the
`_TENANT` policy (`test_rls_plugins.py` passes; the provider's `platform_custom_field*` and
`bank_deletion` have no `tenant_id` by design); every new route carries a permission dependency
and every `/ui` POST its CSRF check; `check:verify` and `custom_field:write` are masked for
customer-scoped sessions and `check:verify` is outside Admin's default grant; a customer-scoped
session reads custom-field values only through `values_for(customer_id=…)` on every path (API,
web lists, exception page, feed, CSV, export) and can't write one by any path; unique fields are a
database constraint; `set_values` and each import row run in savepoints; the teller `cashed` call
claims its verification with a conditional update and presents through `ingest_paid_item` under
the issued-item lock; the cut-off sweep and *Pay all* use `get_for_update`; the re-key signs
AuthnRequests with the current key throughout and accepts the next key only while one exists;
nothing secret enters change requests or audit metadata; bank deletion enumerates
`Base.metadata.sorted_tables` by `tenant_id` under the bank's RLS binding, rows and files in one
transaction with files last, every guard re-checked inside the job, and the Postgres test asserts
zero rows for the bank in every table; the certificates are ECDSA-signed with the file-signing
key and stored in tables that outlive the data; the ended-customer gate in `auth/deps.py` covers
every token path; the 1.30.1 bank-day fix is right; the deadline arithmetic is right for
holidays (a Saturday July 4 not moved; a Sunday one observed Monday), closures, DST and the
holiday-open bank; `after_return_deadline` is one function behind the feed, the webhook and the
event; R29/R17 are added per bank and seeded for new ones; the ML floor and HUMAN-only labels
are as the CHANGELOG says; every GitHub Action is SHA-pinned and both base images
digest-pinned; the final image runs as `openclear` with `--chown` copies and no recursive
`chown`; the notices page renders under autoescape; the ZAP waiver is URL-scoped; the OpenAPI
contract test passes; all ten new tables are in the data dictionary; the audit actions that
aren't security events are the data-only ones (field values, the yearly confirmation).

### C. Features: wrong, dead or missing

| # | Sev | Finding | Where | Status |
|---|---|---|---|---|
| T4 / O4 | Medium | **An offboarded customer's check makes teller verify fail with 500.** `verify` resolves the account through `account_service.get_account_by_number`, which raises `ServiceEnded` once `service_ended_at` is set; nothing catches it. The 1.28.1 CHANGELOG and the guide say this case answers `not_on_positive_pay`; the test sets only `is_active=False`. `mrm_service.replay` and the custom-field import share the exposure. Reproduced. | `services/teller_service.py:140-157`; `services/account_service.py:55-89` | CONFIRMED |
| O5 | Medium | **Service ends at the next housekeeping run, not at midnight.** Sign-in, file-row refusal and teller status key off `service_ended_at` and `is_active`, which only the daily sweep (03:30, server clock) sets. For a US bank whose last day ends at 05:00–08:00 UTC, access continues until the next morning, and `service_ended_at` is then the sweep time, so the 90-day clock and "Service ended on" drift from the promised day. | `workers/scheduler.py:58`; `services/customer_offboarding_service.py:184, 329-341`; `services/account_service.py:63-68`; `auth/deps.py:167` | CONFIRMED |
| K1 | Medium | **The third-party page's license identifiers and notice texts are never checked by a test that runs.** The exact-regeneration test calls `build()`, which exits unless every in-image package is installed at its locked version; CI installs only `dev.txt` and locally the runtime extras aren't installed, so it skips everywhere. The four tests that run compare names and versions only: a wrong `license`, `used_for`, copyright, system-package license or any `requirements/licenses/*.txt` text passes. Mitigated: `lock-refresh.yml` regenerates both files on every lock change, so the exposure is a hand edit to `vendored.json` or a licenses file. | `tests/test_compliance/test_third_party.py:33-89`; `scripts/third_party.py:137-154`; `.github/workflows/tests.yml:49, 101` | CONFIRMED |
| G3 | Low | **The CEO/CIO fallback can never receive a notice.** `set_contacts` refuses a save without at least one `kind="contact"`, so `recipients()` returns the named contacts or, with no rows at all, the administrators; the `("ceo","cio")` branch is unreachable. The hint, the control's text, `incident-notification.md` and CUEC B-IR-6-05 all describe the fallback. | `services/incident_service.py:72-74, 102-112`; `templates/settings/sections/incident-contacts.html:2-5`; `compliance/cuecs.py:421-425` | CONFIRMED |
| G4 | Low | **A notice "sent" to nobody looks sent.** With no contacts and no `admin:manage` holder, `_deliver` records `recipients=[]`, the bank's log says "emailed to nobody", and the operator sees "Notice … sent" with no per-bank recipient count. | `services/incident_service.py:149-172`; `web/routers/platform.py:531-532`; `templates/platform/incidents.html:37-45` | CONFIRMED |
| G5 | Low | **"Offboarding" is offered as a notice kind by hand**, though `KINDS` marks it "never chosen by hand". | `services/incident_service.py:27-28`; `templates/platform/incidents.html:14-16` | CONFIRMED |
| G6 | Low | **Holiday copy vs behaviour.** The CHANGELOG's "never later than the bank's cut-off on that day" reads as the presented day (the code caps at the return day, correctly); the closures hint says Federal Reserve holidays "are always skipped", and `next_cutoff` indeed ignores `open_on_federal_holidays`, so a bank open on holidays gets no cut-off that day (its checks are saved by the cap; its ACH items are due the next day). | `services/cutoff_service.py:133-145, 169-174`; `templates/settings/sections/cutoff.html:33-42` | CONFIRMED |
| G7 | Low | **Three rows for ten contacts.** The form renders 3 contact rows; `MAX_CONTACTS` is 10. A bank with more than 3 (only via a future API) loses the rest on the next save. | `templates/settings/sections/incident-contacts.html:11`; `web/routers/tenant_settings.py:814-818` | CONFIRMED |
| O9 | Low | **A permanently failed deletion job is silent.** After `job_max_attempts` the job is `FAILED`, `BankDeletion` stays `queued`, the page says "It runs at <past time>" forever; no event or control fires. | `workers/jobs.py:243-247`; `templates/platform/offboarding.html:63-66` | CONFIRMED |
| O11 | Low | **`revoke_all_sessions` bumps `token_version`**, signing the person out of every organisation; `stop()` restores `is_active=True` even if the customer was inactive for another reason; `_strip` writes JSON `null` into three NOT NULL JSON columns (readers use `or {}`). | `services/customer_offboarding_service.py:151-154, 184-186`; `services/offboarding_service.py:324-331` | CONFIRMED |
| C4 | Low | **Restoring an archived field bypasses the 25-field and 3-in-list limits.** | `services/custom_field_service.py:311-316` | CONFIRMED |
| C5 | Low | **A mistyped `cf_` header fails every row instead of refusing the file**: 20,000 identical row failures, a COMPLETED status, "0 changed, 20000 failed". The same for an archived field's column. | `services/bulk_import_service.py:382-398`; `services/custom_field_service.py:413-418` | CONFIRMED |
| C6 | Low | **Text values pandas treats as NA vanish from update files** (`NA`, `N/A`, `null`, `None`, `#N/A` → blank → left alone or cleared). Pre-existing parser behaviour, newly exposed to free text. | `bulk_import/tabular.py:40-51` | PLAUSIBLE |
| C7 | Low | **Validation is looser than its messages**: `fromisoformat` accepts `20260101`; the integer regex and `int()` accept non-ASCII digits; `Decimal` accepts `1_000`. Stored values are normalised, so message polish. | `services/custom_field_service.py:108-124` | CONFIRMED |
| C8 | Low | **A `(field, record)` row collision is reported as a unique-value clash** (two concurrent first writes to one field on one account). | `services/custom_field_service.py:450-454` | PLAUSIBLE |
| C9 | Low | **Removed choice options are retired whether or not any record uses them**, so a typo'd option can never go. CF-D11 meant options in use. | `services/custom_field_service.py:211-215` | CONFIRMED |
| T7 | Low | **Trailer semantics are fixed.** `check_trailer` counts every detail line (skipped transaction codes included) and totals signed amounts; a core whose trailer counts debits only, or totals gross, mismatches every file, and the bank's only recourse is to turn the trailer off. | `bulk_import/layouts.py` (`check_trailer`, `TrailerCheck`) | PLAUSIBLE |
| T8 | Low | **Hostile numbers in a posted-check file surface as an unhandled error, not `rejected`**: `int()` on a 4,300-digit trailer count raises `ValueError`, which no layer catches. | `bulk_import/layouts.py` (`_amount`, `_signed_amount`, `check_trailer`); `services/bulk_import_service.py:215-245` | CONFIRMED |
| K5 | Low | **The metadata never advertises an encryption key** (`wantAssertionsEncrypted=False`), so the "decrypt with either key" fallback is reachable only for an IdP configured by hand; the docstring and model comment say the IdP "can encrypt assertions to it". | `auth/saml_service.py:146, 206-208` | CONFIRMED |
| K8 | Low | **`IMAGE_ENV` is hard-coded to x86_64 / 3.13.0**, so an arm64 build's inventory differs from the page; `dev_only` lists a dev package pinned at another version as the runtime one. | `scripts/third_party.py:36-40, 207-208` | CONFIRMED |

### D. Interface

| # | Sev | Finding | Where |
|---|---|---|---|
| O10 | Low | **Dates shown in UTC, not the bank's day.** The customer's banner shows `service_ends_at` as "YYYY-MM-DD HH:MM UTC" (the day after the last day for US banks); the detail page and the Deleted list show UTC dates; the email says `deleted_on = last_day + 91` while `deletion_due` is sweep time + 90. | `templates/base.html:203`; `templates/customers/detail.html:217-222`; `templates/customers/deleted.html:17-18` |
| T9 | Low | **Branch declines show raw result codes to customers**: "customer review", "payee needed", "stop differs" with no explanation. | `templates/exceptions/list.html` (branch-declines block) |
| C10 | Low | **Custom-field forms.** One error banner per save (the first failing field aborts); "(optional)" where a required marker is meant; raw type names (`integer`, `boolean`, `choice`) in the settings table, the upload page and the type select; a choice shows its value as its label; the account fields page runs one query per archived field. Accessibility is otherwise right. | `templates/_macros/custom_fields.html:7-31`; `settings/sections/custom-fields.html:18, 56`; `custom_fields/upload.html:25`; `web/routers/accounts.py:204-206` |
| K3 | — | The re-key page's missing warnings (section B). | `templates/sso/form.html` |

### E. Operations and tests

| # | Sev | Finding | Where |
|---|---|---|---|
| K6 | Low | **`shows_number` still matches inside UUIDs**: hyphens and hex letters count as boundaries, and a UUID4's third group starts with `4`, so `-4001-` appears with probability 1/4096 per UUID on the page. | `tests/items.py:102-106` |
| K7 | Low | **Lock-refresh and CI-image PRs get no CI** (opened with the default `GITHUB_TOKEN`, which doesn't trigger workflows); the required checks appear only after a push to the branch. Not a path to a broken lock on `main`. | `.github/workflows/lock-refresh.yml:27-35`; `ci-image.yml:109-117` |

**Tests missing**, by cluster (each stage below adds its own):

- *Money path (Postgres, `-m concurrency`):* a teller `cashed` racing a file presentment of the same check; two `cashed` calls with one verification; *Pay all* racing the cut-off sweep; a job or request for a bank running during its deletion.
- *Reverse and teller:* the same check presented twice after the first was paid by silence (T1); an ended customer at `/checks/verify` (T4); the shape of `payee_needed` (T2); probing via `amount_differs` (T3); two verifications of one check (T5).
- *Deadlines:* a check presented on day D arriving after the return day's cut-off (G1); `lock_due` after midnight with the webhook's and the CSV's `after_return_deadline` asserted; the holiday-open bank through ingestion; a presentment on a holiday; DST in Phoenix and Honolulu; the ML floor at 199 human decisions.
- *Offboarding:* a SQLite-suite assertion that no row with the bank's `tenant_id` remains in any metadata table (only the Postgres test does it); every `storage.AREAS` prefix cleared (O1); S3 `delete_tenant`; sign-in refusal for an ended customer on each path; reactivation via the API (O2); cooling-off on a protection kind with one operator (O3); the kept-user path of `_forget` (O6).
- *Custom fields:* a customer-scoped write by file with a group that has `custom_field:write`; customer-scoped `feed.csv` and `/decisions/file`; RLS on a bank session writing `custom_field_value`; a negative value through `to_csv` (C1); restore against the limits (C4); an unknown `cf_` header (C5).
- *Incidents and re-key:* incidents under RLS (`issue()` across banks; `acknowledge` with another bank's delivery); an assertion encrypted to the next key during the transition; a response for the old key refused after `finish`; the bank-staff re-key route; a request made stale by a cancel.
- *Compliance artefacts:* a test that fails on a stale license identifier, `used_for`, copyright, system entry or license text (K1); `shows_number` against a page with `-4001-` in a UUID.

## Decisions (recommended)

- **RV-D1. A check's payment is recorded when it becomes final.** When `decision_service.lock`
  locks a check decision whose outcome is *pay* (a person's, *Pay all*'s or the default's), it
  sets the paid item `settlement_status = PAID` and a matched issued item `status = PAID`; a
  locked *return* sets the paid item `RETURNED` (the ACH path already does). Lock time, not
  decide time, because a decision can change until the cut-off and payment is final at the lock.
  `_is_duplicate_paid` and the paid-candidate rule then see every paid check, on reverse and
  ordinary accounts alike. Items locked before this ships are backfilled by a one-off data
  migration from their decision rows.
- **RV-D2. Deletion clears every storage area, from the one list.** `_delete_files` iterates
  `storage.AREAS` (`decision_files` and `ml_artifacts` included) and the certificate lists the
  same; a test walks `AREAS` so a new area can't be missed. The bank is frozen at the start of
  the deletion job: `is_active=False` first (sign-ins and API calls refused from that moment),
  its queued jobs cancelled and running ones waited for (`jobs.check_lease`), then the wipe.
- **RV-D3. An ended customer can't be reactivated sideways.** `upsert_customer` (API) and the
  web toggle refuse `is_active=True` while `service_ended_at` is set, answering 409 with the
  two-admin restore as the way back; `end_service` also deactivates the customer's memberships,
  so a future path that misses the check still can't sign anyone in.
- **RV-D4. Protection-tier changes never cool off.** For `protection` kinds (and every
  provider-scope kind) `request_or_apply` raises `ChangeError("needs a second operator") ` when
  nobody else can approve, instead of scheduling a cooling-off apply. An installation with one
  operator can't offboard a bank until it has two, and the page says so. Sensitive and access
  tiers keep the cooling-off.
- **RV-D5. Service ends at the end of the last day, wherever it's read.** `_refuse_ended`,
  `_enrolled`, the sign-in gate and the teller answer read `service_ends_at <= now` (already the
  bank's end of day in UTC), and `end_service` records `service_ended_at = service_ends_at`, so
  the 90-day clock and the pages agree with the promised day. The sweep still revokes sessions
  and sends the emails.
- **RV-D6. Kept people are anonymised; the bank's own records stay.** When `_forget` must keep
  a user, the `_PERSONAL` rows are deleted outside the savepoint and the user row is reduced to
  its id and a placeholder name (no password hash, phone or email), counted in the certificate
  as "anonymised". Customer deletion keeps `billing_statement` and its lines, `cuec_attestation`,
  `access_review` and its items (nulling the customer FK where one exists); `teller_verification`
  and `change_request` go (the export already holds them). The plan, CHANGELOG and certificate
  say which.
- **RV-D7. A late check gets a short window, never past midnight.** When the capped deadline is
  already past at ingest, `within_return_deadline` returns `min(returns_by, now +
  cutoff_min_review_minutes)` and the item is marked urgent (the reminder goes at once), so a
  person has the minimum review time before the default applies; nothing is ever due after the
  return deadline.
- **RV-D8. The teller answer gives nothing the caller didn't send.** `issued` is omitted when
  the only problem is `payee_needed` and never carries `payee_name` except for `payee_differs`;
  `_watch_for_probing` counts every non-match per teller, branch and client (a person's token
  counts under the user id), and `/checks/verify` gets its own `rate_limit()`. A `match` for a
  check with an unexpired, uncashed `match` verification by another teller answers
  `being_cashed` (a decline), and the guide's "Two branches at once" says what the lock does.
  `cashed` on an already-cashed verification returns 409 with the `paid_item_id` and who cashed
  it. `ServiceEnded` is caught in `verify` (and `mrm_service.replay`) and answers
  `not_on_positive_pay`.
- **RV-D9. One error type for sentences meant for people.** `services/errors.py::UserFacingError`
  (a `ValueError`), which `ChangeError`, `OutsideServiceError`, `RowFieldError` and the other
  service errors that carry sentences for a person inherit; routers catch only it (and the
  named types they already handle) and show a fixed sentence for anything else. One sweep over
  `web/routers/`, with a test that greps for `str(exc)` on a bare `ValueError` or `Exception`.
- **RV-D10. The limiter degrades to the process, and says so.** On a Redis failure `allow()`
  falls back to the in-process limiter for that call, and a control check (`rate_limit_backend`,
  WARN) plus a security event the first time it flips tell the operator.
- **RV-D11. CSV outputs know their types.** `to_csv` takes the field definitions and applies the
  formula guard to text and choice values only; numbers and dates pass as they are. The OpenClear
  CSV layout's columns are listed on the decision-files page and in API.md, and the 1.29.0 entry
  gains an upgrade note about `after_return_deadline`.
- **RV-D12. The third-party comparison runs where it can.** `security.yml` gains a step that
  runs `scripts/third_party.sh --check` (Docker is on the runner) and fails on any difference
  from the committed page and notices; the skipping test stays for local runs that can.
  `IMAGE_ENV` is read from the build's platform; `dev_only` names both versions.
- **RV-D13. Copy says what the code does.** The re-key page warns that finishing before the
  provider has the certificate stops sign-in and can't be undone, and that withdrawing after it
  switched needs the old certificate re-given, with a "test sign-in" link beside Finish; the
  CEO/CIO fallback is either built (two optional rows that `recipients()` uses when no contact
  answers) or removed from the hint, the control, the page and the CUEC (recommended: built, it's
  a dozen lines); the incidents form hides `offboarding`; the Sent table shows recipients per
  bank; the holiday hint and the CHANGELOG line are corrected; branch declines and custom-field
  types get `status_label`s; offboarding dates show the bank's day.

## Stages

Each stage is one pull request. Before merging: the three full suites, Bandit and the dependency
audit (CLAUDE.md), and a Postgres run for the concurrency tests the stage adds.

| Stage | What | Closes |
|---|---|---|
| **RV1. A paid check is paid** | RV-D1: settlement and issued-item status written at `lock`; the backfill migration; the twice-presented reverse test and the amount-mismatch-then-duplicate test; *Pay all*'s guard compares the item ids it showed, not the count; `cashed` 409 with `paid_item_id`. Postgres: teller `cashed` vs a file presentment; two `cashed` with one verification; *Pay all* vs the sweep. | T1, T10, T11 |
| **RV2. The teller answer** | RV-D8 and the `ServiceEnded` catch; `being_cashed` in the `Result` literal, the guide's table, OpenAPI and the SDKs; the probing counter and the route limit; tests for each. | T2, T3, T4/O4, T5, T6 |
| **RV3. Deadlines** | RV-D7 with its tests (a late file; the holiday-open bank through ingestion; a presentment on a holiday; Phoenix and Honolulu); the CSV column documented (RV-D11's second half); `next_cutoff` honours `open_on_federal_holidays` or the hint says why not (recommended: honour it); the ML floor test. | G1, G2, G6, G9 (noted as one-off) |
| **RV4. Bank deletion** | RV-D2: every area, the freeze, the jobs; a failed deletion job raises `bank.offboarding` (high) and shows on the page; the SQLite-suite "no row remains" assertion over the metadata; the `AREAS` walk test; S3 `delete_tenant` test. RV-D4: protection kinds never cool off, with the page copy and a test with one operator. | O1, O3, O7, O9 |
| **RV5. Customer offboarding** | RV-D3, RV-D5, RV-D6 and their tests (reactivation via API and web refused; service ended at midnight on each path; the kept-user anonymisation; the kept tables); dates in the bank's day; `stop()` restores the prior `is_active`; sessions revoked for the customer's memberships only; CO plan, CHANGELOG and certificate text. | O2, O5, O6, O8, O10, O11 |
| **RV6. Custom fields and the limiter** | RV-D11's first half (`to_csv` by type, the `-5` test); RV-D10; hidden keys refused for scoped feed readers (422 like unknown ones); restore re-checks the limits; an unknown or archived `cf_` header refuses the file; `keep_default_na=False` for update files; the validation messages; options retired only when in use; per-field error messages, required markers, `status_label` for types, option labels, one query for the archived fields. | C1–C10 |
| **RV7. Copy and notices** | RV-D13's incident items (the CEO/CIO rows, the hidden kind, recipients per bank, ten contact rows or a limit of three, `incident_contact.confirmed` mapped); branch-decline labels; the re-key warnings and test-sign-in link; the holiday copy. The axe and viewport checks must pass unchanged. | G3, G4, G5, G7, G8, T9, K3 |
| **RV8. Errors, artefacts and the build** | RV-D9 across `web/routers/` with its grep test; RV-D12 (`--check` in `security.yml`, `IMAGE_ENV`, `dev_only`); the bank-staff re-key under the connection's own dual control, or the docstring says why not; `postgres:16` by digest; `_waiver` requires an instance; `shows_number` excludes hex runs; hostile numbers in layouts become `rejected`; the trailer gains "count debits only" and "total gross" options once a core's sample shows the need (PIL plan). | K1, K2, K4, K5 (docstring), K6, K8, K9, T7 (noted), T8 |

**Order.** RV1 first: it's the money path, and the backfill migration wants to ship before more
reverse accounts exist. RV4 and RV5 next, together or back to back: a bank's deletion certificate
that lists five areas is a compliance record that's wrong today. RV2 and RV3 are independent of
everything and of each other. RV6 and RV7 can run beside anything. RV8 touches every router and
goes last on a quiet branch.

**What this plan doesn't do.** No change to the decision model, the change-request model or the
storage layout. The 1.26.0 review's portfolio decisions (FR-D19) stand. The trailer options
(T7) wait for a core's real file (PAID_ITEM_LAYOUTS_PLAN.md).

## Progress log

- 2026-10-31: Plan written from a review of `v1.33.0` (`0bafc661`) covering 1.28.0 to 1.33.0,
  five clusters read end to end, every High and Medium re-checked by hand against the code.
  Three Highs (T1, O1, O2), fifteen Mediums, the rest polish. The RLS coverage test, the
  OpenAPI contract test and the data-dictionary check were run as part of the review and pass.
- 2026-10-31: RV1 built on `review-1-33`. `decision_service.lock` settles the check (`_settle_check`:
  paid or returned; a paid check's outstanding, stale or stopped issued item becomes paid; a void
  is kept), so `_is_duplicate_paid` and the paid-candidate rule see every paid check; migration
  `b8c4d2e6f1a3` brings decisions already final into line, per bank under RLS. *Pay all* posts the
  ids the page listed and pays exactly those. `cashed` on an already-cashed verification answers
  409 with `message`, `paid_item_id` and `cashed_at`. Tests: `tests/test_decisions/
  test_check_settlement.py` (reverse paid then presented again; an accepted amount mismatch then
  two more presentments; a return; a voided check paid), the pay-all and teller tests amended,
  and `tests/test_postgres/test_teller_concurrency.py` (a branch cash against a file presentment;
  one verification cashed twice at once; *Pay all* against the sweep).
- 2026-10-31: RV2 built on `review-1-33`. `issued` is left out when the only problem is `payee_needed`
  and carries the payee only for `payee_differs`; `being_cashed` (a match verified within 15
  minutes and not yet cashed, seen because verifications take turns on the issued item's lock);
  the probing counter counts every decline, by teller, branch, client, and by `user_id` (a new
  column, migration `c9d5e3f7a2b4`) when there's no client; `ServiceEnded` is no longer raised at
  the teller or in an MRM replay (`account_service.find_account_by_number`). Guide, OpenAPI and
  SDKs updated. **Deviation:** no per-route `rate_limit()` on `/checks/verify`: the global limit
  is already 120 a minute per address, and a stricter one keyed the same way would add nothing;
  the counter that matters is the probing one. Also fixed while running the suite for RV1: backing
  out a file whose check was paid by a decision restores the issued check, as a matched one does,
  and a check already returned by a decision can't be backed out (it was always so for a return).
