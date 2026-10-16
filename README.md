<p align="center"><img src="docs/brand/openclear-banner.svg" alt="OpenClear" width="500"></p>
<p align="center"><a href="https://openclear-sandbox.fly.dev/ui/sandbox"><img src="docs/brand/try-the-sandbox.svg" alt="Try OpenClear in a free sandbox: your own bank in about a minute, with sample checks, ACH payments and exceptions, and an API client. Start a sandbox bank at openclear-sandbox.fly.dev" width="760"></a></p>

# OpenClear

*Formerly PosPay. Existing installs keep working after the rename: see
[CHANGELOG.md](CHANGELOG.md).*

[![Tests](https://github.com/chaffed/OpenClear/actions/workflows/tests.yml/badge.svg)](https://github.com/chaffed/OpenClear/actions/workflows/tests.yml)

Multi-tenant positive pay platform (checks + ACH today, extensible to other payment
networks — see `networks/`) with pluggable OCR and an ML-assisted exception review
feedback loop.

This README covers local setup and deployment only. For architecture (data model, the
network-adapter pattern, matching engine rules, ML pipeline design), see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). For the JSON API (`/api/v1/*` — endpoints, auth, permissions,
schemas), see [API.md](API.md). For what changed in each release, see
[CHANGELOG.md](CHANGELOG.md). For implementing a new bank or a new customer —
prerequisites and step-by-step setup, also available as an in-app guided checklist at
`/ui/wizard/bank` and `/ui/customers/{id}/wizard` — see [RUNBOOK.md](RUNBOOK.md).

## Contents

- [Message from the author](#message-from-the-author)
- [Screenshots](#screenshots)
- [Quickstart](#quickstart)
- [Running tests](#running-tests)
- [Bulk file uploads](#bulk-file-uploads)
- [Bulk-loading check images](#bulk-loading-check-images)
- [Backing out a bulk upload](#backing-out-a-bulk-upload)
- [Users, security groups, and cross-tenant access](#users-security-groups-and-cross-tenant-access)
- [Querying and exporting users (access recertification)](#querying-and-exporting-users-access-recertification)
- [Customers: segregating a tenant's own business clients](#customers-segregating-a-tenants-own-business-clients)
- [Per-tenant branding](#per-tenant-branding)
- [Immutable action log](#immutable-action-log)
- [Compliance and control testing](#compliance-and-control-testing)
- [Billing](#billing)
- [Authentication](#authentication)
- [Signing keys](#signing-keys)
- [Reverse proxy / WAF deployment](#reverse-proxy--waf-deployment)
- [Docker](#docker)
- [Demo tenant](#demo-tenant)
- [Postgres](#postgres)
- [MSSQL](#mssql)
- [Running more than one instance](#running-more-than-one-instance)
- [Reporting](#reporting)
- [Logs and security events](#logs-and-security-events)
- [Background jobs](#background-jobs)
- [Plugins](#plugins)
- [Upgrade and downgrade support](#upgrade-and-downgrade-support)
- [Web UI](#web-ui)
- [Architecture at a glance](#architecture-at-a-glance)
- [License](#license)

## Message from the author
This was built using Claude Code. I am not a developer/programmer. I do have 20 years of bank systems, payments, and check processing experience. This is the Positive Pay system I want. I've also rolled in security enhancements I have never seen in commercial positive pay systems. My goal is to demonstrate banking software needs to be modernized.

## Screenshots

<table>
<tr>
<td width="50%">

**Dashboard**
![Dashboard](docs/screenshots/dashboard.png)

</td>
<td width="50%">

**Exceptions queue**
![Exceptions queue](docs/screenshots/exceptions.png)

</td>
</tr>
<tr>
<td width="50%">

**Reviewing an exception**
![Exception review](docs/screenshots/exception_detail.png)

</td>
<td width="50%">

**Issued items**
![Issued items](docs/screenshots/issued_items.png)

</td>
</tr>
<tr>
<td width="50%">

**ACH transactions**
![ACH transactions](docs/screenshots/ach_transactions.png)

</td>
<td width="50%">

**ML models (admin)**
![ML models](docs/screenshots/admin_ml_models.png)

</td>
</tr>
<tr>
<td width="50%">

**Users**
![Users](docs/screenshots/users.png)

</td>
<td width="50%">

**Organization settings**
![Organization settings](docs/screenshots/settings.png)

</td>
</tr>
<tr>
<td width="50%">

**Billing**
![Billing](docs/screenshots/billing.png)

</td>
<td width="50%">

**A customer's billing statement**
![Billing statement](docs/screenshots/billing_statement.png)

</td>
</tr>
</table>

<details>
<summary>More screens (accounts, customers, security groups, stop payments, check-image bulk upload, audit log, fee schedule, login)</summary>

| | |
|---|---|
| ![Accounts](docs/screenshots/accounts.png) Accounts | ![Customers](docs/screenshots/customers.png) Customers |
| ![Security groups](docs/screenshots/security_groups.png) Security groups | ![Stop payments](docs/screenshots/stop_payments.png) Stop payments |
| ![Check image bulk upload](docs/screenshots/check_images_bulk.png) Check-image bulk upload | ![Audit log](docs/screenshots/audit_log.png) Audit log |
| ![Fee schedule](docs/screenshots/billing_fees.png) Fee schedule | ![Login](docs/screenshots/login.png) Login |

</details>

Screenshots are generated from a seeded demo tenant, not hand-captured — see
`scripts/generate_screenshots.py` if you change the UI enough to make these stale:

```bash
pip install -e ".[dev]"
playwright install chromium
python scripts/generate_screenshots.py
```

This spins up the app against a throwaway SQLite database (never your real
`.openclear-run/` or `openclear.db`), seeds a demo bank with realistic accounts, issued items,
exceptions, ACH activity, and users, and drives a real headless browser through each
screen to (re)write the PNGs under `docs/screenshots/`.

## Quickstart

**One-click (SQLite, zero manual setup)**:

- **macOS**: double-click `run_openclear.command` (opens Terminal.app and runs it there).
- **Windows**: double-click `run_openclear.bat` (opens a Command Prompt window).
- **Linux**: run `./run_openclear.sh` from a terminal — most file managers don't run a
  double-clicked `.sh` file in a terminal by default (varies by distro/desktop
  environment and usually needs "Allow executing file as program" enabled first in the
  file's properties), so this one isn't meant to be double-clicked.

On first run it creates a virtual environment, installs everything, runs migrations,
prompts you to create an organization + admin login, then starts the server and opens
your browser to it. Every re-run after that just starts the server — safe to run any
number of times. All three wrappers just find a Python interpreter and hand off to the
same OS-agnostic `scripts/launcher.py`; if you'd rather skip the wrapper, run that
directly on any platform:

```bash
python3 scripts/launcher.py
```

Everything this creates — the virtual environment, the SQLite database, uploaded check
images, and trained ML models — lives under `.openclear-run/` next to the project, never in
the source tree itself. **To fully reset and leave the checkout exactly as cloned, just
delete that one folder:**

```bash
rm -rf .openclear-run
```

**Manual setup**, if you'd rather control each step yourself (and are fine with `.venv`,
`openclear.db`, `data/`, and `ml_artifacts/` living directly in the project root as
persistent local dev state, rather than one disposable folder):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

alembic upgrade head
uvicorn openclear.main:app --reload
```

Web UI at `http://localhost:8000/ui/login`. JSON API docs at `http://localhost:8000/docs`.
Tesseract (`brew install tesseract` / `apt install tesseract-ocr`) must be on `PATH` for
check-image OCR to work — it's a system binary, not pip-installable; the launcher warns
if it's missing but still runs.

**PDF export of the documentation** (the "Download PDF" link on `/ui/docs/end-user` and
`/ui/docs/admin`) needs the optional `pdf` extra plus system libraries WeasyPrint depends
on — Pango, Cairo, and GLib. `run_openclear.command`/`scripts/launcher.py` installs the
`pdf` extra automatically; for a manual setup, install it yourself:

```bash
pip install -e ".[pdf]"
brew install pango          # macOS
apt install libpango-1.0-0 libcairo2  # Debian/Ubuntu
```

On macOS, Homebrew's lib directory isn't on the dynamic linker's default search path, so
even with Pango installed, `import weasyprint` would otherwise raise `OSError`
(`services/doc_pdf_service.py` works around this automatically — no manual
`DYLD_FALLBACK_LIBRARY_PATH` needed). Without the `pdf` extra installed at all, every
other page still works — only the PDF download routes show a "not available" message
instead of a file.

The admin PDF's ER diagrams (`/ui/docs/admin/data-dictionary`) are pre-rendered PNGs, not
generated at request time — WeasyPrint doesn't execute JavaScript, so the live
Mermaid+pan/zoom the interactive page uses can't render there. See
`scripts/render_schema_diagrams.py` if you change that page's diagram content:

```bash
pip install -e ".[dev]"
playwright install chromium
python scripts/render_schema_diagrams.py
```

This spins up the app against a throwaway SQLite database, drives a real headless
browser to the Data Dictionary page with pan/zoom disabled so each diagram renders at
full natural size, and (re)writes the PNGs under
`src/openclear/static/generated/schema-diagrams/`.

## Running tests

```bash
pytest
```

`.github/workflows/tests.yml` runs the same suite automatically on every push/PR to
`main` (Python 3.11 and 3.13, with `tesseract-ocr`/Pango/Cairo installed so the
OCR- and PDF-export-dependent tests actually run rather than skip), and once more
against Postgres.

To run it against Postgres yourself, point `OPENCLEAR_TEST_DATABASE_URL` at an empty
database. **It is wiped**: the schema is rebuilt with `alembic upgrade head`, so the
migrations are tested too. `tests/test_postgres/` (skipped otherwise) then checks row-level
security with a non-superuser role:

```bash
pip install -e ".[dev,postgres]"
docker run -d --name openclear-pg-test -e POSTGRES_USER=openclear -e POSTGRES_PASSWORD=openclear \
  -e POSTGRES_DB=openclear_test -p 127.0.0.1:55432:5432 postgres:16-alpine
OPENCLEAR_TEST_DATABASE_URL=postgresql+psycopg://openclear:openclear@127.0.0.1:55432/openclear_test pytest
```

For performance numbers, `scripts/perf/seed_volume.py` fills a throwaway database with
banks, checks, exceptions and decisions at a chosen scale, and `scripts/perf/measure.py`
times pages, ingestion, training, exports and key queries against it (see SCALE_PLAN.md).

## Bulk file uploads

Accounts (`/ui/accounts/bulk`), issued items (`/ui/issued-items/bulk`), presented checks
(`/ui/paid-items/bulk`) and ACH transactions (`/ui/ach/transactions/bulk`) accept bulk
uploads, in addition to the JSON API's `/bulk` endpoints (which take a pre-built JSON
array, not a file):

- **Delimited or Excel** (`bulk_import/tabular.py`): any comma/tab/semicolon/pipe file
  or `.xlsx`/`.xls`, header row required, column names case/spacing-insensitive, any
  order. Delimiter is detected by counting candidates in the header line — deliberately
  not `csv.Sniffer`/pandas' generic auto-detection, which on a short sample can pick the
  most-frequent character in the text itself rather than an actual delimiter.
- **NACHA** (`bulk_import/nacha.py`, ACH only): a standard 94-character ACH file, with
  or without line breaks, padding lines of 9s and all. Extracts the batch header fields
  (company id/name, SEC code, effective date) and entry detail fields (DFI account number,
  amount, individual id, transaction code, trace number) needed to create transactions,
  including IAT (international) entries, and checks each batch's and the file's control
  totals (entries plus addenda, entry hash, debit and credit totals). A file whose totals
  don't add up is rejected whole; ADV (accounting advice) files aren't supported. Checked
  entry by entry against Moov's open source ACH implementation on its sample files
  (`tests/fixtures/moov-ach/`).

- **Layouts** (`bulk_import/layouts.py`, issued items): a customer's own format, described
  once in Settings → Issue-file layouts: delimited (any delimiter, header or not, columns by
  name or position), fixed-width (start and length per field, an optional record prefix, and
  lines to skip for a header or trailer) or Excel (a sheet). Amounts with a decimal point,
  implied cents or a separate cents column; named date formats (`MMDDYY`, `YYYYMMDD`…); a
  void flag; leading zeros dropped if asked. The layout builder works from a sample file and
  previews the result before saving, and starts from a short library of common shapes. An
  upload records a copy of the layout it used. The web form, the API (`format=layout` with a
  `layout_id`; `GET /api/v1/bulk-uploads/layouts`) and the drop folder (the default layout)
  all use them.

Every row/entry carries its own account number (not a single account picked once for the
whole file) — resolved against your existing accounts, so one file can span multiple
accounts. Unmatched account numbers, and any other bad row, are reported individually
without failing the rest of the file. Each failed row names the field at fault, and can be fixed in the
browser and imported again (`/ui/bulk-uploads/<id>/fix`): only the fixed rows are imported,
each recorded as a `bulk_upload_correction` with who made it, and backing the upload out
covers them. The uploaded file itself never changes. Every upload page also takes a file
dragged onto it.

**Files are imported in the background** (`services/bulk_import_service.py`). The upload
stores the file and checks it can be parsed; a file that can't be is rejected on the form
with nothing imported. Otherwise a background job (see "Background jobs") imports it and
the browser goes to the upload's page (`/ui/bulk-uploads/<id>`), which shows progress,
refreshes itself, and then shows the result: how many rows succeeded, each row that failed
and why, and a CSV of every row's outcome. The job commits 1,000 rows at a time
(`OPENCLEAR_BULK_IMPORT_CHUNK_ROWS`), each row in its own savepoint, so a bad row fails on
its own and a job that's interrupted resumes at the first row it hadn't committed. New
exceptions from an upload send each reviewer **one** summary notification, not one per
exception. The automatic dropbox import (`services/dropbox_import_service.py`) uses the
same path.

Both upload forms have a **"create missing accounts"** checkbox: when checked, any
account number in the file that doesn't already exist is created automatically instead of
failing that row. Delimited/Excel files may include an optional `account_name` column,
used as the new account's name; NACHA files carry no such field, so accounts it creates
are named `Account <number>`. The same new account number appearing in multiple rows of
one file is only created once.

**Every submitted file is saved and signed** (`bulk_import/file_storage.py`,
`bulk_import/signing.py`, `services/bulk_upload_file_service.py`) — issued items, ACH
(tabular and NACHA), and the user bulk upload (see below) all keep the original bytes on
local disk (`OPENCLEAR_BULK_UPLOAD_STORAGE_DIR`, default
`./data/bulk_uploads`, consolidated under `.openclear-run/` by the quickstart launcher) plus
a SHA-256 fingerprint and an ECDSA P-256 signature made with a server-held private key
(`OPENCLEAR_FILE_SIGNING_PRIVATE_KEY_PATH`; see "Signing keys"). The file is copied to disk
in pieces and hashed as it's written, so a large upload is never held in memory. This is saved
**even when the file is rejected outright** (bad format, no data rows) — a malformed
submission is still evidence of what was actually sent, so it's recorded before parsing
ever runs, with a link to it shown right on the error page. Every results/error page links
to a detail view (`/ui/bulk-uploads/<id>`) that re-verifies the signature against a fresh
read of the file **on every visit** — not a cached flag — so tampering after upload (even
a direct edit of the file on disk) shows up as a mismatch, and the original file can be
downloaded byte-for-byte from there. Gated by the same permission that gated the original
upload (`account:write`, `issued_item:write`, `paid_item:write`, `ach_transaction:write`,
`check_image:write` or `user:manage`).

## Bulk-loading check images

`/ui/check-images/bulk` (gated by **both** `paid_item:write` and `check_image:write` —
stricter than every other bulk import in this app, since this one creates two different
resource types in a single step, unlike the single-image upload above, which only ever
links to a paid item that already exists) accepts two formats. **Both create a new
`paid_item` per check** — running it through the same matching engine as any other
presented item — **and** attach its image in the same step, rather than requiring the
paid item to already exist: a real image cash letter *is* the presentment, not a
follow-up step.

- **ZIP file** (`bulk_import/zip_import.py`): a zip containing exactly one manifest file
  (CSV/TSV/Excel — columns `account_number`, `check_number`, `amount`, `presented_date`,
  `front_image_filename`, optional `back_image_filename`) plus the image files it
  references by filename (matched by basename, case-insensitively, anywhere in the
  archive). Supported image formats: single- or 2-page TIFF, JPG, PNG
  (`bulk_import/images.py`) — a 2-page TIFF is split automatically (page 1 = front, page
  2 = back) unless `back_image_filename` is given explicitly, which always wins.
- **X9.37 image cash letter** (`bulk_import/x937.py`): a Check 21 cash letter file in
  the standard layout (DSTU X9.37), as banks, processors and the Federal Reserve exchange
  them: length-prefixed records in EBCDIC or ASCII, and also line-separated or
  concatenated records. Images in Image View Data (Type 52) records stay binary. Bundle
  (Type 70), cash letter (Type 90) and file (Type 99) control totals are validated, and a
  mismatch rejects the whole file. Return cash letters aren't imported (a file with return
  records is rejected with a clear message). The field positions were checked byte for
  byte against Moov's open source X9 implementation and its sample files
  (`tests/fixtures/moov/`), in both directions. A cash letter is read through a memory
  map, with each check's images read only as that check is imported, so a large file
  isn't held in memory; a zip's images are likewise read as each row needs them. The
  account number is the On-Us field before the MICR on-us symbol (`/`), without its
  spacing dashes (a bank that stores account numbers with the dashes still matches); the
  check number is the Auxiliary On-Us field (business checks) or, failing that, the On-Us
  part after the symbol (personal checks). Banks lay out on-us data differently, so the
  ZIP+CSV format above is the explicit-column fallback.

Every incoming image, regardless of source format, is decoded and re-encoded to PNG
before storage (`bulk_import/images.py`) — this is what makes the front/back download
links on a check image's detail page (`/ui/check-images/<id>/front` and `/back`) and
downstream OCR work identically no matter what format the original file arrived in.
Same per-row/per-item transaction isolation as every other bulk importer (one bad check
doesn't roll back the batch), and the same signed-original-file audit trail
(`BulkUploadKind.CHECK_IMAGES`) described above.

## Backing out a bulk upload

Every bulk upload (issued items, ACH transactions, users, accounts, check images) can be
undone from its detail page (`/ui/bulk-uploads/<id>`, "Back out this upload"), reusing
the same signed-file/audit infrastructure described above. This is only possible for
uploads processed **after** this feature shipped — `bulk_upload_created_record` (new)
durably links a `BulkUploadFile` to every row it successfully created, something no
earlier version of this app tracked; an older upload's detail page shows "nothing can be
automatically backed out" instead of the button.

Backing out is **one-way** (matching every other void/cancel/revoke action's own
one-way design — nothing here supports "undo the undo") and, per resource type, either
really reverses what was created or is a record-only annotation, depending on whether
this app has any reversible concept for that resource at all:

- **Issued items**: voided (the same `void_issued_item` a manual void uses) — skipped,
  not an error, if already voided or already paid (voiding a paid item would leave its
  matching paid item in an inconsistent state).
- **Users**: the membership created deactivates (the same `deactivate_membership` the
  manual button uses) — skipped if already inactive. Only memberships created directly
  by the bulk file are tracked; one confirmed later via the separate cross-tenant
  confirmation step is a deliberate, independent admin action and isn't automatically
  backed out (it can still be deactivated individually, same as any other membership).
- **Accounts / ACH transactions**: **record-only.** Neither has any deactivation or
  reversal concept in this app at all (no `Account.is_active`, no `AchTransaction`
  status field) — backing these out marks the tracking record and logs an audit entry,
  without inventing new account-lifecycle or ACH-reversal behavior that wasn't asked
  for.
- **Check images**: the hardest case, since a bulk check-image row's real object is the
  `paid_item` it created (see above), which can have already changed *other* rows.
  Reversing it flips a matched `PaidItem.settlement_status` to a new `REVERSED` value
  (excluded from duplicate-payment detection, same as `RETURNED`); if it had flipped a
  linked issued item to `PAID`, that reverts to `OUTSTANDING` — but only if nothing else
  changed that issued item's status since (left alone and noted otherwise). If it had
  spawned an exception in the review queue that's still `OPEN`/`PENDING_APPROVAL`, that
  exception is auto-withdrawn (new `ExceptionStatus.WITHDRAWN` — shown in the exceptions
  queue, and rejected by the recommend/decide routes with a clear message rather than
  the generic "already decided" one); one that's already been paid/returned/escalated by
  a human is left untouched and noted, never silently overridden.

Requires the same permission that gated the original upload
(`issued_item:write`/`ach_transaction:write`/`user:manage`/`account:write`), plus, for
check images specifically, **both** `paid_item:write` and `check_image:write` — matching
that upload route's own stricter dual-permission gate, since backing one out can touch
both resource types.

## Users, security groups, and cross-tenant access

Access control is a set of per-tenant **security groups** (`auth/permissions.py`,
`services/security_group_service.py`), not a fixed role enum — each group is a named,
editable set of permission keys drawn from a single catalog covering every action in the
app (read/write per resource, plus `exception:recommend`/`exception:decide`,
`admin:manage`, `user:manage`, `security_group:manage`). Every new tenant is seeded with 4
default groups reproducing the old fixed roles — **Admin** (everything), **Preparer**
(read/write except deciding exceptions or managing users/groups/admin), **Approver**
(read-only plus `exception:decide`), **Viewer** (read-only) — fully editable from there
via `/ui/security-groups`. Permissions are resolved from the database on **every
request** (`auth/deps.py::decode_and_build_context`), not baked into the JWT, so editing a
group's permissions — or deactivating a user — takes effect on the very next request, not
after the token expires.

**Users** (`/ui/users`) can be added one at a time or via CSV bulk upload
(`/ui/users/bulk`, columns: `email`, `security_group`, `password`), reusing the same
`bulk_import/` infrastructure as issued items/ACH. A `User` is a **global login identity**
(email is unique platform-wide, not per-tenant) with zero or more `TenantMembership` rows,
each pointing at one tenant and one security group there — this is what makes **cross-tenant
access** possible: the same person, same password, can hold membership (with a different
security group) in more than one tenant. Login still takes an organization slug + email +
password exactly as before; once logged in, **"Switch organization"** in the nav
re-mints tokens for any other tenant you're an active member of, with no re-entry of
password or WebAuthn (see below).

Adding a user by an email that already belongs to an identity in a *different* tenant
never attaches it silently — both the single-add form and the bulk CSV surface an explicit
confirmation step ("grant this existing user access to this organization?") before a
membership is created, and that confirmation re-resolves the identity by email
server-side rather than trusting anything the client posted.

WebAuthn credentials are still registered **per tenant-membership**, not once for the
whole identity — a user with memberships in two tenants currently registers a security key
separately in each. Unifying that to one identity-wide credential set is a reasonable
fast-follow, deliberately out of scope for the cross-tenant-membership work.

## Querying and exporting users (access recertification)

`GET /api/v1/users` (gated by `user:manage`) returns the same data `/ui/users` shows —
one row per `TenantMembership` (email, security group, customer scope or "bank-wide",
active/deactivated status, when the membership was created, and `last_login_at`) — built
by reusing `user_service.list_tenant_users` directly, so the API and the web page can
never drift apart. A user with several memberships in one tenant (bank-wide plus
per-customer scopes) correctly appears once per membership, matching how access is
actually granted; this is meant for an access-review/recertification process that needs
to answer "who has access to what, and have they actually used it."

`User.last_login_at` is now actually populated — it existed as a column before this but
nothing ever set it. It's stamped at the moment a login *completes* (password-only, or
after WebAuthn MFA finishes), on both the web and API channels; token refresh and
"switch organization" don't count as a fresh login and don't touch it.

The same list can be exported straight from `/ui/users` as **CSV** or **JSON**
(`/ui/users/export.csv` / `.json`, same permission, same underlying data — no separate
filtering, it exports exactly what the page shows).

## Customers: segregating a tenant's own business clients

A `Tenant` is the bank; a `Customer` (`/ui/customers`, gated by a dedicated
`customer:manage` permission) is one of the bank's own business clients within that
tenant — e.g. "Acme Corp," a company whose accounts and check/ACH activity the bank
processes. Customers are optional: accounts created without one are "house" accounts,
visible only to tenant-wide staff exactly as before this feature existed, so an existing
installation with no customers behaves identically to today.

**One mechanism serves two use cases.** `TenantMembership` gains an optional
`customer_id`: `NULL` is today's exact behavior (tenant-wide staff, sees everything in the
tenant), and a real value scopes that membership's security group to just that one
customer's data. The same field expresses both "a bank employee restricted to servicing
specific customers" and "a customer's own employee logging in to see only their own
company's data" — there's no separate portal or permission catalog, just a narrower scope
on an ordinary membership. A person can hold several memberships in the same tenant (one
tenant-wide, plus overrides per customer, or several customer-scoped ones with no
tenant-wide membership at all) —**"Switch organization"** (`/ui/switch-tenant`) doubles as
"switch customer scope," listing every membership and which customer (or "bank-wide") each
is scoped to. Logging in still takes just a slug + email + password; if more than one
membership exists for that tenant, login defaults to the tenant-wide one if present, else
the earliest-created customer membership, and the switcher reaches any other one — a
login-time picker for the (rare) multi-membership case is a reasonable fast-follow,
deliberately out of scope here.

**Segregation is denormalized, not just joined.** `customer_id` is stamped onto `account`
and onto every table that hangs off an account — `issued_item`, `stop_payment`,
`paid_item`, `ach_authorization_rule`, `ach_transaction` — the same way `tenant_id`
already is, always derived server-side from the referenced account at creation time, never
trusted from a request. `repositories/base.py::CustomerScopedRepository` is the
enforcement point: when a session's `customer_id` is set, every read on those six tables
is additionally filtered to it; when it's `None` (tenant-wide), it behaves exactly like
plain tenant scoping. A customer-scoped caller referencing another customer's `account_id`
directly — not just through a filtered dropdown — gets a clean 404, the same
anti-enumeration posture as cross-tenant isolation, because every service that creates a
child record resolves the parent account through this same repository rather than a raw
lookup.

**No new "what" permissions — only "whose."** A security group's permissions
(`Preparer`, `Approver`, etc.) mean the same thing whether a membership is tenant-wide or
customer-scoped; scoping only narrows *whose* data they apply to. One hard-coded exception:
`user:manage`, `security_group:manage`, `tenant:manage`, `customer:manage`,
`admin:manage`, and `audit_log:read` are unconditionally stripped from the resolved
permission set whenever a membership is customer-scoped
(`auth/permissions.py::CUSTOMER_SCOPE_MASKED_PERMISSIONS`, enforced in
`auth/deps.py::decode_and_build_context`) — regardless of what the underlying security
group nominally contains, so even a customer-scoped membership using the full "Admin"
group can never reach tenant-admin surfaces like `/ui/users` or `/ui/audit-log`.

**Bulk loading** extends the existing CSV infrastructure rather than adding a new one:
accounts (`/ui/accounts/bulk`, columns `account_number`, `name`, optional
`customer_number`, optional `ach_debit_block_mode`) and users
(`/ui/users/bulk`, existing columns plus an optional `customer_number`) both resolve a
human-readable customer number to the tenant's internal customer record the same way
issued-item/ACH bulk uploads already resolve account numbers. A customer-scoped uploader
can only ever create rows in their own scope — a `customer_number` column that names a
different customer is rejected, not silently reassigned.

**Per-account access.** Within a customer, the bank can limit one of the customer's people
to some of its accounts (Users → Edit → Choose accounts, or
`PUT /api/v1/users/{membership_id}/accounts`; `services/account_access_service.py`). It's
applied to the request's database session, like the tenant binding (`db/account_scope.py`):
every query on that session sees only those accounts and what's on them (issued items, stop
payments, paid items, ACH rules and transactions, exceptions, decisions and check images),
and only their own bulk uploads and data exports. Their background imports and exports run
under the same limit, and notifications and deadline reminders reach them only for those
accounts. Moving the person to another customer puts them back to all accounts.

**Customers managing themselves** (CUSTOMER_ADMIN_PLAN.md). Per customer, the bank can let
the customer's own administrators manage its people (`customer_user:manage`; Settings →
People: invitations by email, roles, accounts, deactivation) and its sign-on
(`customer_sso:manage`; Settings → Sign-on: its own OIDC or SAML connection, verified email
domains, SCIM, switching off passwords) and its API clients (`customer_api_client:manage`;
Settings → API clients: credentials for its own systems, with an offered role, all or some of
its accounts, read-only or not, a secret or a key pair; the bank sees them marked *theirs* and
can revoke them). Each permission is masked unless the bank has turned that customer's switch
on, and they never reach beyond the customer:
- the bank can approve new people first (Settings → New customer users, or per customer):
  anyone the customer's side adds waits, unable to sign in, until the bank approves them
  under Approvals → New customer users;
- only roles the bank marks *offered to customers*, and no editing of roles;
- nobody linked silently: people are invited, and someone who already exists accepts with
  their own password;
- a password reset, unlock or "sign out everywhere" only for someone who belongs to this
  customer alone (`db/tenancy.py::memberships_elsewhere`, which also counts correctly under
  Postgres RLS);
- a customer-managed connection or directory creates people only in verified email domains;
- password sign-in goes off only from a session signed in through sign-on, and the bank can
  always turn it back on;
- a session started through a customer's sign-on is confined to that customer (the `cfd`
  token claim): switching organization means signing in again there.

Not yet done, documented as an accepted v1 limitation: Postgres RLS policies on the six
customer-scoped tables enforce `tenant_id` only, not `customer_id` — the repository layer
above is the primary, tested enforcement point, same posture as tenant isolation's own RLS
today.


**The customer hub.** The Customers list puts customers with items to decide first, soonest
deadline first, with their open items, items due today, the last issued-check file and
what happens at the deadline. "No issue file in N days" finds customers who may have
stopped sending files. From a customer, **Send a reminder now** messages everyone who can
act on its open items.
## Per-tenant branding

Each tenant can customize a logo, favicon, accent color, and display name from
Settings → Branding, `/ui/settings/branding` (gated by a dedicated `tenant:manage` permission, not folded into
`admin:manage`, so a security group can be scoped to branding alone). Uploaded images are
stored on local disk under `OPENCLEAR_TENANT_ASSET_STORAGE_DIR` (default
`./data/tenant_assets`, consolidated under `.openclear-run/` by the quickstart launcher, same
as check-image storage) — the DB only ever stores the path and content-type, never the
blob. The accent color reuses the single `--accent` CSS custom property `app.css` already
threads through every button/link/nav highlight, applied via a small inline `<style>`
override, so no CSS rewrite was needed.

Branding shows throughout the logged-in app shell (nav logo/name, page titles, browser-tab
favicon, accent color) **and** on the login page itself — via a tenant slug in the URL
(`/ui/login/<slug>`), not Host-header/subdomain routing. A slug-based login link needs no
DNS or reverse-proxy infrastructure and works identically locally and in production,
unlike a subdomain-per-tenant approach, which this project deliberately doesn't build
toward given its local-first, one-click-launcher design. An unknown or inactive slug falls
back to the plain generic login form rather than an error. Logo/favicon bytes are served
by two public (unauthenticated) routes keyed by slug, `/ui/branding/<slug>/logo` and
`/ui/branding/<slug>/favicon` — a company logo isn't sensitive, and the login page needs to
show it before any session exists, so both the pre-auth login page and the authenticated
app shell hit the exact same serving routes.

## Immutable action log

Every state-changing action — through **either** the web UI or the JSON API — is recorded
to a per-tenant, tamper-evident action log (`/ui/audit-log`, gated by a dedicated
`audit_log:read` permission that's Admin-only by default, unlike every other `*:read`
permission, since "who did what" is more sensitive than any single resource's own data).
Covers create/void/cancel/revoke/upload/recommend/decide across issued items, stop
payments, paid items, check images, ACH authorizations/transactions, exceptions/decisions,
accounts, users, security groups, and tenant settings — one entry per successfully-created
row for bulk uploads too, not one entry for the file as a whole (the file itself already
has its own signed audit record — see "Bulk file uploads" above).

Entries form a **hash chain**, not independent per-row signatures: each entry's
`entry_hash` is an ECDSA P-256 signature (made with a dedicated key pair,
`audit_log_signing_private_key_path` / `audit_log_signing_public_key_path`, distinct from
every other signing key in this app) over its own fields plus the *previous* entry's hash. A chain — not independent signatures — is what's needed here,
because the real threat to an action log is deletion or reordering, not just editing one
row: an independent per-row signature can't detect a row being deleted outright, but a
broken chain link can. `services/audit_log_service.py::verify_chain` (surfaced as "Verify
chain" on the audit log page) walks every entry for a tenant and recomputes each hash from
scratch — proof nothing has been edited, deleted, or reordered since it was written, not a
cached flag. This is tamper-**evidence**, not OS-level tamper-*prevention* (no DB
triggers/grants are revoked) — the same posture as bulk-upload file signing.

Each bank's entries are numbered (`sequence`, unique per bank), and that's the order the
chain is verified in. Two requests at one bank writing at the same moment can't both chain
onto the same entry: on Postgres they queue on a per-bank lock, and on any database the
unique number makes the loser retry rather than fork the chain. Verification reads the log
in batches, so memory stays flat however long it gets.

Logging calls live in the route handlers (both `web/routers/*.py` and `api/v1/*.py`),
right after the mutating service call succeeds and before that request's `db.commit()` —
so the audit entry and the business change commit together atomically, and every route
already has the actor/tenant/channel it needs without any shared service function having
to take on a new required parameter.

## Compliance and control testing

For a bank's compliance staff, auditors and examiners (COMPLIANCE_PLAN.md;
[docs/compliance](docs/compliance/README.md) maps these onto FFIEC, Nacha, SR 11-7 and
third-party risk guidance):

- **Segregation of duties** (Settings → Segregation of duties). Whoever submits items can't
  decide them, and whoever manages users can't define what groups grant. This is checked on
  groups and per item: the person who presented an item can't decide it.
  - In *warn* mode (where every bank starts) a breach is allowed and logged.
  - In *enforce* mode it's refused with `ERR_SOD_VIOLATION`.
  - Under enforce, turning a matching rule on or off and activating a model wait for a second
    admin (Settings → Changes).
- **Model risk** (Settings → Model risk):
  - *replay* runs a past X9.37 or NACHA file through today's rules and compares the result
    with what happened;
  - *anomaly capture* checks that synthetic fraud is caught.

  Both are dry runs, rolled back. Turn on the daily anomaly capture with
  `OPENCLEAR_ENABLE_CONTROL_TEST_SCHEDULER=true`.
- **Controls** (Settings → Controls, `GET /api/v1/controls/status`, `openclear controls`): each
  control's live PASS / WARN / FAIL with its evidence. Record how the host encrypts disks and
  the database in `OPENCLEAR_STORAGE_ENCRYPTION_ATTESTATION`. The page groups the checks under
  OpenClear's controls, each filed under NIST SP 800-53 ([docs/compliance/controls.md](docs/compliance/controls.md),
  `GET /api/v1/compliance/controls`), and what they rely on the bank, its customers and its host
  to do is the CUEC inventory ([docs/compliance/cuecs.md](docs/compliance/cuecs.md)).
  38 checks: some look at the bank as it is (hourly), some at every item in a period (nightly,
  over the previous day: dual control, two-person changes, replays before changes, support
  access, customers' timeliness). Every scheduled run is kept for seven years
  (`OPENCLEAR_CONTROL_RESULT_RETENTION_DAYS`), with a 30-day strip on each check's page; a FAIL
  opens a deviation, where someone with `compliance:manage` records the cause and the response;
  and any past period can be tested on demand from the data OpenClear already keeps.
- **Evidence export** (`GET /api/v1/audit/export`, `openclear audit export`): a signed JSON or
  PDF bundle for a period.
- **Secrets in the database** are AES-256-GCM. To rotate the key, move the old value to
  `OPENCLEAR_SSO_ENCRYPTION_KEY_PREVIOUS`, set a new `OPENCLEAR_SSO_ENCRYPTION_KEY` and
  restart: stored values are re-encrypted at startup.

## Billing

OpenClear produces what a bank charges its customers for positive pay (Billing in the
Organization menu, `/api/v1/billing` in API.md). The bank builds a fee schedule from a
catalog of metrics: per customer, per user, per account, per issued item or file, per item
evaluated, per exception, per item returned, per stop payment, per image, per notification
and more, each with a unit price, optional step tiers, a monthly minimum and its own AFP
Service Code. "Add starter fees" fills in the usual positive pay services at zero.

Each customer is flagged **account analysis** (charges go to the bank's analysis system,
which offsets them with earnings credits), **hard charge** (debited from its billing
account) or **exempt**, with a bank-wide default. Per customer the bank can also set a
discount, a negotiated price or a waiver for any fee. Tiers and minimums apply across all
the customer's accounts.

Statements are calculated live for any period. Finalizing a month freezes them; finalizing
again saves a replacement version. From the finalized month the bank downloads the billing
report (CSV), an analysis feed (CSV), a hard-charge posting file for the core (CSV), and an
ISO 20022 **camt.086** Bank Services Billing message, the standard successor to the X12 822
account analysis file. A customer's user with `billing:read` sees that customer's own
statement and PDF. OpenClear doesn't calculate earnings credits or post debits itself.

The platform operator can bill each bank too, from its own fee schedule (including a
per-bank monthly fee), through `/api/v1/platform/billing` with a `billing`-scoped platform
key. BILLING_PLAN.md has the research and the decisions.

## Platform staff, the operator view and support access

The people who run an installation aren't members of any bank. `scripts/platform_staff.py`
makes someone platform staff (no screen can):

```sh
python scripts/platform_staff.py ops@yourcompany.example --role operator
python scripts/platform_staff.py helpdesk@yourcompany.example --role support
python scripts/platform_staff.py helpdesk@yourcompany.example --remove
```

They sign in at `/ui/platform/login` (the same lockout as a bank sign-in, a separate session).

- **The operator view** (`/ui/platform`, operators): every bank with its customers, open
  and overdue exceptions, queued and failed jobs, last import, controls and security-event
  delivery; and **Set up a bank**, which creates it with its default groups and return
  reasons and a first admin who chooses their own password on first sign-in.
- **Support access** (instead of impersonation): a bank admin grants a named support person
  access under Settings → Support access, for a reason and up to 72 hours. It's read-only
  (what the bank's staff can see, except secrets, users, groups, settings and the action
  log) unless write access is approved by a second admin, and even then it never covers
  deciding items. The support person opens the bank from `/ui/platform` and works under
  their own name, with a banner saying whose access it is and until when; ending it, or
  its time running out, ends the session on the next request. Nobody can grant or approve
  their own access. Every grant, approval, use and end is in the bank's action log, as a
  security event, and in the evidence export.

## Authentication

**A bank's systems** (its core, its online banking provider) don't use a person's
password: an admin creates an **API client** (Settings → API clients) with a security group
and, optionally, a customer, and the system exchanges its client id and secret for a
short-lived token at `POST /api/v1/oauth/token` (OAuth 2.0 client credentials; API.md).
It acts through a service identity nobody can sign in as, with exactly its group's
permissions; revoking it takes effect at once. Instead of a secret, a client can sign in
with a key pair (`private_key_jwt`: it signs a short assertion with a private key that never
leaves its systems; API.md).

**People** sign in with username + password (bcrypt-hashed) issuing JWTs (`api/v1/auth.py`), with an optional
FIDO2/WebAuthn second factor (`api/v1/webauthn.py`, `auth/webauthn_service.py`):

- `POST /auth/webauthn/register/options` + `/register/verify` — register a security key
  (authenticated with a normal access token)
- `GET /auth/webauthn/credentials`, `DELETE /auth/webauthn/credentials/{id}` — manage
  registered keys
- Once a user has at least one registered key, `POST /auth/login` no longer returns real
  tokens after the password check — it returns `mfa_required: true` and a short-lived
  `mfa_token` (5 min, carries no permissions) instead. That token authenticates
  `POST /auth/webauthn/login/options` + `/login/verify`, which — once the assertion
  verifies — issue the real access/refresh token pair.
- Users with no registered key log in exactly as before (`mfa_required: false`); this is
  additive, not a breaking change for existing accounts.

WebAuthn requires a real browser to drive `navigator.credentials.create()/get()` — there's
no backend-only way to test the full ceremony against an actual authenticator. The test
suite (`tests/test_auth/webauthn_helpers.py`) instead hand-constructs cryptographically
valid registration/authentication responses (COSE-encoded EC key, CBOR attestation/
authenticator data, DER ECDSA signature) to exercise the real server-side verification
path without a browser. `webauthn_rp_id`/`webauthn_origin` default to `localhost` /
`http://localhost:8000` — set both to your real domain before deploying, or every
registered credential will fail verification against the wrong origin.

**Single sign-on** connections can be set up for the whole bank or for one business customer.

- **Protocols:** OIDC (Okta, Entra ID, Ping, Google, Keycloak) or **SAML 2.0**
  (`auth/saml_service.py`).
- **Group mappings:** the person's groups map to security groups at every sign-in.
- **SCIM 2.0** at `/scim/v2` provisions and deactivates people from the bank's directory.
- **Signing out at the identity provider:** it ends the person's OpenClear sessions, through
  OIDC back-channel logout or SAML single logout.
- **Sign-in started at the identity provider:** for example, a portal tile. With SAML it's
  opt-in per connection.
- **The online banking sign-on profile:** a *customer claim* on a bank-wide connection names
  each person's customer. With it, the online banking platform's server can exchange a
  person's ID token for a token acting for them (RFC 8693; see API.md).

**SSO (OIDC) issuer addresses** must be public `https://` URLs. OpenClear refuses to save an
issuer that's `http://`, `localhost`, or a private/internal IP address, and every time it
contacts an identity provider (discovery, signing keys, token exchange) it re-checks what the
hostname resolves to and won't connect to a non-public address (`auth/outbound_http.py`).
Otherwise anyone able to edit an SSO connection could make the server send requests into its
own network. To test against a local identity provider (e.g. Keycloak on
`http://localhost:8080`), set `OPENCLEAR_OIDC_ALLOW_PRIVATE_HOSTS=true`. This is for local use
only, and a production deployment refuses to start with it on. The server also doesn't use
proxy environment variables for these requests, so it needs direct outbound HTTPS to the
identity provider.

For the full set of `/api/v1/*` endpoints (issued items, stop payments, paid items,
check images, ACH, exceptions/decisions, admin, users), the permission each one requires,
and request/response schemas, see [API.md](API.md).

## Signing keys

Four things get cryptographically signed: JWTs (login sessions), bulk-upload files
(tamper-evidence), the immutable audit log (its hash chain — see "Immutable action
log" below), and signed Written Statement of Unauthorized Debit (WSUD) attestations.
Each uses its own ECDSA P-256 (ES256) key pair rather than a shared secret
string, so a leaked key can't also be used to forge the others, and — unlike a
guessable string — a real key pair can't accidentally ship as a usable default.

For local dev and the test suite, `dev_keys/` is a checked-in, deliberately public key
pair set (see `dev_keys/README.md`) — zero setup required. **Never use it for a real
deployment.** Generate your own before deploying:

```
python scripts/generate_keys.py --output-dir keys
```

This prints the eight `OPENCLEAR_*_PRIVATE_KEY_PATH`/`OPENCLEAR_*_PUBLIC_KEY_PATH` env vars to
set. Prefer `openssl` instead? The equivalent for each of the four pairs (`jwt`,
`file_signing`, `audit_log_signing`, `wsud_signing`) is:

```
openssl ecparam -genkey -name prime256v1 -noout -out keys/<name>_private.pem
openssl ec -in keys/<name>_private.pem -pubout -out keys/<name>_public.pem
```

Then set `OPENCLEAR_ENVIRONMENT=production` — this is what actually turns on the check
(`config.py::assert_production_safe`, run at app startup): a production deployment
still pointing at `dev_keys/`, or still using the default `OPENCLEAR_SSO_ENCRYPTION_KEY`
(a separate, plain random secret — it encrypts stored SSO client secrets rather than
signing anything, so it stays a string, not a key pair), refuses to start. Local dev
(`scripts/launcher.py`) and the test suite both explicitly set
`OPENCLEAR_ENVIRONMENT=development`, so neither is affected by this check.

Rotating a key pair later is a one-time, expected cost, not a bug: rotating the JWT
key logs out every active session; rotating the file-signing or audit-log key means
anything signed under the old key stops re-verifying (fine for a pre-launch system with
no real history yet — a live system's key-rotation strategy is a separate, deliberately
out-of-scope design question from this initial hardening pass).

`assert_production_safe` also refuses to start in production for two other
still-at-their-checked-in-default conditions, unrelated to signing keys:

- **`OPENCLEAR_OCR_PROVIDER` set to a stub.** Only `tesseract` (the default) is a working
  OCR provider — `textract` and `azure_document_intelligence` both exist as real,
  installable extras (`pip install openclear[textract]`/`openclear[azure-di]`) but their
  `.extract()` is a bare `NotImplementedError` (see `ocr/textract_provider.py` /
  `ocr/azure_di_provider.py`'s own docstrings for what wiring up a real cloud call would
  need). Without this check, choosing either in production would only fail the moment
  someone actually uploaded a check image, not at startup.
- **WSUD e-signature text still at its placeholder default.** `OPENCLEAR_WSUD_CONSENT_
  DISCLOSURE_TEXT`/`OPENCLEAR_WSUD_ATTESTATION_TEXT` default to placeholder legal language
  implementing only the *structural* elements the federal E-SIGN Act requires — **not
  reviewed by a lawyer.** Have your own counsel review and supply real text via those two
  env vars before relying on this for a real Written Statement of Unauthorized Debit
  attestation. Changing this text doesn't affect any already-signed statement — each one
  snapshots exactly what was shown and signed at the time.

## Reverse proxy / WAF deployment

**The full guide is [docs/guides/edge.md](docs/guides/edge.md)**: origin lockdown, the settings,
the WAF exceptions path by path, bot management, the edge features to turn off, caching,
sizes and timeouts, and a go-live checklist. Check a deployment with
`python scripts/edge_check.py https://openclear.bank.example --origin <origin>`: it needs no
account, changes nothing, and fails if the edge blocks legitimate requests, caches pages,
injects scripts, alters security headers, or lets requests around it.

**Rate limits.** Every request is rate-limited (`web/rate_limit.py`):
- a signed-in person or API client as themselves: `rate_limit_per_principal_per_minute`, 600 by default;
- anyone else by IP address: `rate_limit_per_minute`, 120 by default;
- stricter limits on a few routes (markdown preview, the OAuth token endpoint).

Counts are kept in memory in each process. With several processes, share them through Redis
(`OPENCLEAR_RATE_LIMIT_BACKEND=redis`; see "Running more than one instance").

**The caller's address.** Rate limits, the action log, security events, JSON logs and a WSUD
signer's IP all use the same address, worked out once per request (`web/request_context.py`).
By default it's the TCP connection's own address, which a client can't fake. Behind a proxy
or an edge network, every connection comes from the proxy, so tell OpenClear how to find
the caller:

| In front of OpenClear | Settings |
|---|---|
| Nothing | Leave the defaults. |
| A reverse proxy or load balancer that appends to `X-Forwarded-For` (nginx, an AWS ALB, Fly) | `OPENCLEAR_TRUSTED_PROXY_COUNT`: the number of proxy hops, usually `1`. OpenClear takes that many entries from the *right* of the header (the ones your proxies added), never a client-supplied value further left. |
| An edge network with its own client-IP header (Cloudflare `CF-Connecting-IP`, Akamai `True-Client-IP`) | `OPENCLEAR_CLIENT_IP_HEADER` (the header) and `OPENCLEAR_TRUSTED_PROXY_CIDRS` (the edge's address ranges, comma-separated). The header is trusted only on connections from those ranges. |

**Locking the origin down.** Make sure only the edge can reach OpenClear, for example:
- a Cloudflare Tunnel;
- mutual TLS from the edge;
- a firewall allowing only the edge's ranges.

Then, either:
- record how it's done in `OPENCLEAR_EDGE_ATTESTATION`; or
- have the edge add a secret header: `OPENCLEAR_EDGE_SECRET`, with `OPENCLEAR_EDGE_SECRET_PREVIOUS`
  while rotating, and `OPENCLEAR_EDGE_SECRET_HEADER`, default `X-OpenClear-Edge-Secret`. Requests
  without it are refused, apart from `/health`.

Settings → Controls → "Edge network set up" fails when edge headers arrive but OpenClear isn't
set up for them, and warns without a lockdown.

**Request ids.** Every response carries `X-Request-ID`: a trusted edge's own id (`CF-Ray`,
`X-Akamai-Request-ID`) or a new one. It's also in the logs, on error pages, in the action log
and in security events, so a WAF block or an error can be matched to the request behind it.

**Keep-alive and caching.**
- The Docker image keeps idle connections open for `OPENCLEAR_KEEPALIVE_SECONDS` (75 by
  default). Raise it above your load balancer's or edge's idle timeout, or users see
  sporadic 502s.
- Versioned static files (`?v=`) are sent with a year-long immutable cache.
- Pages and API responses are never cached (`no-store`).

**Long requests.** Edges give up on a slow origin after about 100 seconds (Cloudflare) or
120 (Akamai). So these run as background jobs, with a run to poll and a download:
- a replay of a file over `OPENCLEAR_LONG_REQUEST_SYNC_BYTES` (1 MB);
- an evidence export longer than `OPENCLEAR_LONG_REQUEST_SYNC_DAYS` (32);
- verifying the action log.

**Error pages.** Every error on a web page gets OpenClear's own page, with a message that
fits it, a way back and a reference to quote to support: 404, 405, an expired form (403),
a broken address (422), a file over the size limit (413), too many requests (429, with
`Retry-After`). The API answers JSON for all of them. When the database can't be reached,
OpenClear answers **503** with `Retry-After`, and `/health` answers 503 too, so a load
balancer stops sending it traffic.

When OpenClear can't answer at all (restarting, overloaded), whatever is in front of it
shows its own page. `src/openclear/static/errors/` has self-contained 502, 503 and 504 pages
for it to show instead: no scripts or other files, so they work from anywhere. They're
also served at `/static/errors/503.html`, and so on.
- **Cloudflare:** custom error pages.
- **Akamai:** a failover page.
- **nginx:** `error_page 502 503 504`.

WAF_PLAN.md has the rest of the plan for running behind an edge.

## Docker

Two images, `Dockerfile` (production) and `Dockerfile.demo` (public demo) — the demo one
layers on top of the production one rather than duplicating its build, so there's exactly
one place that installs dependencies:

```bash
docker build -t openclear:latest .
docker build -t pospay-demo:latest -f Dockerfile.demo .   # only if you want the demo image too
```

Both run with `OPENCLEAR_ENVIRONMENT=production` baked in — including the demo image, since
the demo tenant serves real public traffic and runs the exact same OCR/ML/storage code
path a real tenant would (see "Reverse proxy / WAF deployment" above and
`services/demo_tenant_service.py`'s own module docstring). That means both need everything
[Signing keys](#signing-keys) above describes, supplied at deploy time, never baked into
the image:

- The four signing key pairs (`python scripts/generate_keys.py --output-dir keys`,
  mount `keys/` into the container, e.g. at `/secrets/keys`, and set all eight
  `OPENCLEAR_*_KEY_PATH` env vars to point there)
- A random `OPENCLEAR_SSO_ENCRYPTION_KEY`
- Real, counsel-reviewed `OPENCLEAR_WSUD_CONSENT_DISCLOSURE_TEXT` /
  `OPENCLEAR_WSUD_ATTESTATION_TEXT`
- `OPENCLEAR_WEBAUTHN_RP_ID` / `OPENCLEAR_WEBAUTHN_ORIGIN` set to your real domain
- For the demo image only: `OPENCLEAR_DEMO_TENANT_ENABLED=true` (already set by
  `Dockerfile.demo`) and `OPENCLEAR_DEMO_TENANT_PASSWORD` (a real secret — set it yourself,
  it has no default)

A container that's missing any of the first four refuses to start at all
(`assert_production_safe`) rather than silently serving with this repo's own public
`dev_keys/`.

The image declares one volume, `/data` — everything the app writes to disk (the SQLite
database by default, check images, bulk uploads, ML model artifacts, tenant branding
assets, data exports) lives under it, so mount a real volume there or every reset/restart
loses everything:

```bash
docker run -d \
  -p 8000:8000 \
  -v pospay_data:/data \
  -v /path/to/your/keys:/secrets/keys:ro \
  -e OPENCLEAR_JWT_PRIVATE_KEY_PATH=/secrets/keys/jwt_private.pem \
  # ...the other seven OPENCLEAR_*_KEY_PATH vars, same pattern...
  -e OPENCLEAR_SSO_ENCRYPTION_KEY=... \
  -e OPENCLEAR_WSUD_CONSENT_DISCLOSURE_TEXT=... \
  -e OPENCLEAR_WSUD_ATTESTATION_TEXT=... \
  -e OPENCLEAR_WEBAUTHN_RP_ID=your-domain.example.com \
  -e OPENCLEAR_WEBAUTHN_ORIGIN=https://your-domain.example.com \
  openclear:latest
```

Point `OPENCLEAR_DATABASE_URL` at Postgres instead of the SQLite default the same way any
other deployment would (see "Postgres" below) — rebuild with
`--build-arg EXTRAS=.[postgres,pdf]` first so the driver's actually installed.

**Creating the first tenant** isn't a route this app exposes over HTTP on purpose (see
"Users, security groups, and cross-tenant access" above) — for the production image, it's
a one-time manual step after the container is up:

```bash
docker exec -it <container> python -c "
from openclear.db.session import get_session_factory
from openclear.services.provisioning_service import create_tenant_with_admin
session = get_session_factory()()
create_tenant_with_admin(session, tenant_name='Your Bank', tenant_slug='your-bank', admin_email='admin@example.com', admin_password='...')
session.commit()
"
```

The demo image needs no such step — `main.py`'s startup seeds the demo tenant
automatically (`services/demo_tenant_service.py::ensure_demo_tenant`) whenever
`demo_tenant_enabled=true`.

**Run a single container** unless you're on Postgres and have read "Running more than one
instance" below: the rate limiter is per process, and on SQLite (the image's default) every
container would also run every scheduled job.

`docker-compose.yml` at the repo root is a separate, narrower thing — a local convenience
for testing against Postgres instead of SQLite (runs in development mode with the
checked-in `dev_keys/`, zero setup), not a production deployment descriptor. Don't use it
as a template for a real deployment; use the `docker run` example above instead.

## Demo tenant

A persistent, fully-functioning sales-demo organization (`services/demo_tenant_service.py`)
— real accounts, issued/paid items, exceptions, ACH activity, users, and its own trained
per-customer ML model, safe to hand a prospect or put on the open web, since it resets
itself. This isn't Docker-specific — the settings below work with the plain one-click
launcher too, just set them as env vars before running it:

- `OPENCLEAR_DEMO_TENANT_ENABLED=true` — makes app startup seed the demo tenant if one
  doesn't already exist yet (`main.py`'s lifespan, idempotent on every later restart).
- `OPENCLEAR_DEMO_TENANT_PASSWORD=...` — required for the above; there's no default, since
  there's no safe hardcoded password for something this public. Deliberately meant to be
  shared, not kept secret — this is a demo tenant's whole point.
- `OPENCLEAR_DEMO_TENANT_SESSION_MINUTES` (default `60`) — how long the demo can sit idle
  before it resets.
- `OPENCLEAR_DEMO_TENANT_RESET_INTERVAL_MINUTES` (default `60`, `0` to turn off) — the demo
  also resets on this fixed schedule, since a demo that visitors keep using is never idle.

**Resets** happen three ways: on the fixed schedule above (anyone signed in at that moment
is sent back to the sign-in page); automatically, the moment anyone next tries to log into
the demo tenant after it's sat idle past the session window (before credentials are even
checked, so a prospect never lands mid-reset); or manually, via a "Reset now" button an
`admin:manage` user sees on `/ui/admin` — useful right before a scheduled demo rather than
waiting out the idle window. Either way, a reset wipes every DB row belonging to the demo
tenant *and* purges everything it wrote to disk (check images, bulk uploads, ML model
artifacts, branding assets) before reseeding from scratch — real content (including
whatever an OCR run extracted from an uploaded check image) never outlives one idle
window. Scoped tightly to whichever tenant is actually flagged `is_demo` in the database —
looked up fresh on every reset, never caller-supplied, so this can't be pointed at a real
tenant. A reset also puts the organization's own settings (banner and login messages,
colors, dual control, password rules) back to a new demo's values. More on this from the
demo tenant's own perspective in the in-app Admin Documentation once you have one running
(`/ui/docs/admin`).

**What's locked in the demo**: everyone shares the same published credentials, so actions
that would let one visitor lock out or disrupt the others are refused (web and API alike).
These are SSO and security-group changes; editing or granting other users' access; the
shared account's password, security keys, and "sign out other devices"; branding and
session timeouts; data exports; and ML retraining/activation. The full list lives in one
place, `web/demo_guard.py`. Everything else, including the whole positive-pay workflow,
works normally.

**Sharing a link**: `/ui/login/{tenant_slug}` is a tenant-branded login page (any tenant,
not demo-specific) — pre-fills the slug and shows that tenant's own name/accent color, so
`https://your-demo-host/ui/login/your-demo-slug` is a cleaner link to hand someone than the
generic `/ui/login` form.

**Deploying one publicly**: see [Docker](#docker) above for `Dockerfile.demo` — it's the
production image with `OPENCLEAR_DEMO_TENANT_ENABLED=true` layered on, needing everything a
real deployment needs (real signing keys, `OPENCLEAR_SSO_ENCRYPTION_KEY`, WSUD text — the
demo tenant serves real public traffic and runs the exact same code path a real tenant
would, so it gets no shortcuts). `fly.toml` at the repo root is a working, minimal-cost
example for [Fly.io](https://fly.io) specifically — one machine on the smallest VM size
this app runs reliably on, a persistent volume for `/data`, and
`OPENCLEAR_TRUSTED_PROXY_COUNT=1` already set for Fly's own edge proxy (see "Reverse proxy /
WAF deployment" above). The demo runs on SQLite, so whatever host you use, keep it to one
instance (see "Running more than one instance").

## Postgres

```bash
pip install -e ".[dev,postgres]"
docker compose up -d postgres
OPENCLEAR_DATABASE_URL=postgresql+psycopg://openclear:openclear@localhost:5432/openclear alembic upgrade head
```

Or bring up the whole stack (app + Postgres) with `docker compose up --build`.

**Row-Level Security**: on Postgres, migrations enable RLS (`FORCE ROW LEVEL SECURITY`)
on the single-tenant operational tables (`account`, `issued_item`, `stop_payment`,
`check_image`, `paid_item`, `ach_authorization_rule`, `ach_transaction`,
`security_group`, `tenant_membership`, `bulk_upload_file`, `audit_log_entry`,
`ach_return_reason`, `wsud_statement`, `wsud_statement_transaction`) as
defense-in-depth alongside the primary
tenant-isolation mechanism (the repository-layer filter in `repositories/base.py`, which
is what's actually under test in `tests/test_api/test_cross_tenant_isolation.py`).
`exception_item`/`decision` (the shared ML model trains across every organization that
chose it, see `ml/train.py`) and `user` (a global login identity with no single-tenant row-ownership
story — see "Users, security groups, and cross-tenant access" above) are deliberately
excluded.

**Connect the app as a restricted role so RLS is actually enforced.** A superuser, or a
role with `BYPASSRLS`, skips every policy, which leaves only the repository filter (as on
SQLite and SQL Server). Run migrations as the owner, and the app as a separate role:

```sql
CREATE ROLE openclear_app LOGIN PASSWORD '...' NOSUPERUSER NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO openclear_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO openclear_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO openclear_app;
-- tables added by later migrations need the same grants (or ALTER DEFAULT PRIVILEGES)
```

How it works: each request, scheduled job and platform-operator call runs its database
work bound to one bank (`db/tenancy.py`), re-applied at the start of every transaction.
Switching organization can also see the signed-in user's own memberships in other banks.
The full test suite runs against Postgres in CI, and `tests/test_postgres/` exercises the
web UI, the API, switching organization, the disposition sweep, the dropbox import, usage
metrics and creating a bank as a restricted role, with other banks' data present.

## MSSQL

Requires the Microsoft ODBC Driver (17 or 18) installed at the OS level — not available
via pip alone (see Microsoft's docs for your platform). Also requires a running SQL
Server instance; a Linux container (`mcr.microsoft.com/mssql/server`) is the easiest way
to get one for local dev.

```bash
pip install -e ".[dev,mssql]"
OPENCLEAR_DATABASE_URL="mssql+pyodbc://openclear:<password>@localhost:1433/openclear?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes" alembic upgrade head
```

Known friction points, not yet exercised against a live instance in this build:
- `UNIQUEIDENTIFIER` type mapping for UUID primary/foreign keys — SQLAlchemy's generic
  `Uuid` type should handle this, but hasn't been verified against real MSSQL here.
- The Postgres RLS migration is a no-op on MSSQL (dialect-gated) — MSSQL deployments rely
  solely on the repository-layer filter for tenant isolation, same as SQLite.
- Alembic's autogenerate has known rough edges on MSSQL around identity columns and
  server-side defaults; review generated migrations before applying against MSSQL.

## Running more than one instance

By default, run **one** app process. `scripts/launcher.py`, the Docker image and
`fly.toml` all do. Running several processes or containers against one database
(`uvicorn --workers N`, several replicas behind a load balancer) is only supported on
**Postgres**, and two things change:

- **Scheduled jobs** (ML retrain, dropbox import, notification sending, expired-disposition
  sweep, demo reset) run on whichever instances enable their `OPENCLEAR_*` flags. On Postgres,
  each run first takes a per-job advisory lock (`workers/leader_lock.py`), so one instance
  runs each tick and the rest skip it. If an instance dies mid-job, Postgres releases its
  lock with the connection. On SQLite and SQL Server there is no such lock: every instance
  runs every job, so those deployments must stay a single process. (You can also enable the
  scheduler flags on just one instance and leave them off on the rest.)
- **Rate limits** (`web/rate_limit.py`, see "Reverse proxy / WAF deployment" above) are
  counted per process by default, so with N processes a client can make up to N times the
  configured limit. To share one count, run Redis and set
  `OPENCLEAR_RATE_LIMIT_BACKEND=redis` and `OPENCLEAR_RATE_LIMIT_REDIS_URL=redis://...`
  (`pip install 'openclear[redis]'`). If Redis can't be reached, requests are allowed and
  the error is logged, so a Redis outage doesn't take the app down. Either way, enforce the
  real limit at your proxy or WAF too.
- **Files** (check images, bank logos, bulk-upload originals, data export archives and
  trained ML models) go wherever `OPENCLEAR_STORAGE_BACKEND` says (`storage.py`):
  - `local` (the default): the `*_storage_dir` and `ml_artifact_dir` directories in
    `config.py`. Every instance must see the same files, so point them at one shared
    volume.
  - `s3`: an S3-compatible bucket shared by every instance, with no shared disk needed
    (`pip install 'openclear[s3]'`). Set `OPENCLEAR_S3_BUCKET`, and optionally
    `OPENCLEAR_S3_PREFIX`, `OPENCLEAR_S3_REGION`, `OPENCLEAR_S3_ENDPOINT_URL` (for MinIO,
    Cloudflare R2 and other providers) and `OPENCLEAR_S3_ACCESS_KEY_ID` /
    `OPENCLEAR_S3_SECRET_ACCESS_KEY` (leave them empty to use boto3's own credentials:
    the environment, or an instance role). Give the credentials read, write, list and
    delete on that bucket (or prefix) only.
  - `azure`: an Azure Blob Storage container, the same way (`pip install 'openclear[azure]'`).
    Set `OPENCLEAR_AZURE_STORAGE_CONTAINER`, optionally `OPENCLEAR_AZURE_STORAGE_PREFIX`, and
    either `OPENCLEAR_AZURE_STORAGE_CONNECTION_STRING` or `OPENCLEAR_AZURE_STORAGE_ACCOUNT_URL`
    (`https://<account>.blob.core.windows.net`), which signs in with a managed identity, the
    environment or the Azure CLI (azure-identity). Give that identity the *Storage Blob Data
    Contributor* role on the container only. The app refuses to start in production with
    `azure` and no container or credentials.

  The database stores a reference to each file: a local path, `s3://bucket/key` or
  `azblob://container/name`. Reads go by the reference, so switching to `s3` or `azure`
  doesn't break files already on disk.
  To move them, run `python scripts/move_files_to_object_storage.py --dry-run`, then
  without `--dry-run`, then with `--delete-local` once you've checked. It's safe to run
  again. The dropbox inbox (`auto_import_dropbox_dir`) is still a directory: put it on the
  instance that runs the dropbox scan, or on a shared volume.
- **Database connections:** each web process and each `openclear-worker` keeps a pool of
  up to `OPENCLEAR_DB_POOL_SIZE` (5) plus `OPENCLEAR_DB_MAX_OVERFLOW` (10) connections.
  Postgres must allow at least that many times the number of processes, plus headroom for
  migrations and admin tools. For many processes, put PgBouncer in front in **transaction**
  pooling mode for the web processes and workers. Row-level security works through it,
  because the bank is re-applied with `set_config(..., true)` at the start of every
  transaction, and the audit log's lock is transaction-scoped. The one exception is the
  scheduler's leader lock (`workers/leader_lock.py`), which holds a session-level
  advisory lock for the length of a job. Give whichever instances run scheduled jobs a
  direct connection to Postgres, or session pooling. `OPENCLEAR_DB_POOL_RECYCLE_SECONDS`
  (1800) and `OPENCLEAR_DB_POOL_TIMEOUT_SECONDS` (30) are there too.
- **Background jobs** (OCR, data exports, dropbox scans, retraining) are rows in the
  database's `job` table, claimed by workers, so any instance's worker can run any job,
  and a job whose worker dies is picked up again. By default each web process runs one
  worker thread (`OPENCLEAR_WORKER_MODE=in_process`). To size workers separately from web
  traffic, set `OPENCLEAR_WORKER_MODE=external` on the web processes and run
  `openclear-worker` as its own process or container (as many as you need; on Postgres they
  claim jobs with `SKIP LOCKED`, so they never run the same job twice).

Everything else a request depends on (sessions, CSRF, WebAuthn challenges, the demo tenant's
idle-reset clock) lives in the database or in signed cookies, so a request can land on any
instance. Only the OIDC discovery and signing-key caches are per process, and each instance
simply fills its own.

## Reporting

OpenClear has no reporting dashboard of its own: banks report from their business intelligence
platform (Power BI, Tableau, Looker, Qlik, Superset…), fed from the API into their warehouse.
[docs/guides/bi.md](docs/guides/bi.md) covers a read-only API client, what to pull and how to
keep it current (cursor feeds for decisions, the action log and security events; paged lists),
the data model, row-level security for the bank, its customers and the operator, and reading a
replica on a self-hosted install.

## Logs and security events

**Logs.** `OPENCLEAR_LOG_FORMAT=json` writes one JSON object per line to stdout, with:
- the time (UTC), level, logger and message;
- during a request, its id, the caller's address, the bank and who is signed in;
- an access line per request (method, path, status, duration).

Request bodies, query strings, tokens and cookies are never logged. A container log
collector (Splunk's OpenTelemetry Collector, Fluent Bit, Datadog, CloudWatch) can ship them as
they are. The default, `text`, is unchanged.

**Security events.** Sign-ins and their failures, lockouts, second factors, tokens,
refusals (missing permissions, rate limits, segregation of duties), and changes to who may
do what are recorded as security events (`services/security_events.py`; SIEM_PLAN.md has
the catalog). Each one has the caller's address, the request id and who, and never a
password, token, secret or account data. Floods (failed tokens, refusals) are sampled to one
event a minute per source, with a count.

A bank reads its own events at `GET /api/v1/security-events`, and its action log, with each
entry's signature, at `GET /api/v1/audit-log`. Both are feeds with a cursor, and need
`audit_log:read`. `openclear events tail --follow --cursor-file f` prints the events as JSON
lines for a log collector. The operator reads every bank's, and those belonging to no bank,
at `GET /api/v1/platform/security-events` (platform key, `operations` scope).

Events are kept for `OPENCLEAR_SECURITY_EVENT_RETENTION_DAYS` (30), except those a SIEM
destination hasn't received yet.

**Delivery to a SIEM.** Events can also be pushed, in order and at least once, to syslog
(RFC 5424 over TLS, as JSON, CEF or LEEF), a Splunk HTTP Event Collector, or an HTTPS
endpoint. Each bank adds its own destination in **Settings → Security event forwarding**
and receives only its own events; the operator can set one for the whole installation with
`OPENCLEAR_SIEM_*`. Turn delivery on with
`OPENCLEAR_ENABLE_SECURITY_EVENT_DELIVERY_SCHEDULER=true`. The control "Security events
delivered" fails when a destination falls behind. [docs/guides/siem.md](docs/guides/siem.md)
covers Splunk, Sentinel, QRadar, Elastic and a syslog relay, with suggested detections.

## Background jobs

Slow or CPU-heavy work (OCR on an uploaded image, building a data export, a bank's dropbox
scan, retraining a model) runs as a background job (`workers/jobs.py`), not inside the web
request. A job is a row in the `job` table: it's committed with whatever queued it, claimed
by a worker, retried with back-off if it fails (up to `OPENCLEAR_JOB_MAX_ATTEMPTS`, default
5), and queued again if its worker stops sending heartbeats for
`OPENCLEAR_JOB_STALE_AFTER_SECONDS` (default 300), e.g. after a crash or redeploy.

Where jobs run is `OPENCLEAR_WORKER_MODE`:

| Mode | What runs jobs | Use it for |
|---|---|---|
| `in_process` (default) | a worker thread inside each web process | a single process: the launcher, the Docker image, the Fly demo. Nothing extra to run. |
| `external` | `openclear-worker` processes you start separately | sizing background work independently of web traffic, on Postgres |
| `inline` | whatever queued the job, right away | the test suite |

`openclear-worker --once` runs every job that's due and exits (for cron). A job that has
used all its attempts stays `failed`, with its error, until the platform operator retries it
through `/api/v1/platform/jobs` (API.md).

Exports stream to disk in batches, so a bank of any size can export. The only bound is the
time limit, `OPENCLEAR_DATA_EXPORT_TIMEOUT_SECONDS` (default an hour; a bank can set its own).

### Housekeeping and data growth

Some tables grow with every request without being anyone's record. A daily housekeeping
run (`services/housekeeping_service.py`; turn it on with
`OPENCLEAR_ENABLE_HOUSEKEEPING_SCHEDULER=true`, or call `workers.tasks.housekeeping_job`
from cron) clears them out:

| What | Kept for | Setting (days; 0 keeps them forever) |
|---|---|---|
| Finished background jobs | 30 days | `OPENCLEAR_RETENTION_FINISHED_JOBS_DAYS` |
| Sent (or failed) notifications | 180 days | `OPENCLEAR_RETENTION_NOTIFICATIONS_DAYS` |
| Bulk-upload row results (the upload, its file and counts stay) | 365 days | `OPENCLEAR_RETENTION_BULK_ROW_RESULTS_DAYS` |
| Data export archives (a full copy of a bank's data) | 30 days, then marked expired | `OPENCLEAR_RETENTION_DATA_EXPORTS_DAYS` |

It never deletes a bank's business records (presented checks, ACH, exceptions,
decisions, issued items): how long to keep those is each bank's compliance decision.

**The audit log** can be archived instead (`OPENCLEAR_AUDIT_ARCHIVE_AFTER_DAYS`, off by
default; `services/audit_archive_service.py`). Entries older than that are verified, then
moved into a signed, gzipped JSON Lines file in storage, and a checkpoint records where
the live chain carries on. A broken chain is never archived. Nothing is lost: the audit
log page lists each archive for download, and "Verify" checks the archives too. The
newest entry always stays in the table.

## Plugins

> **Try it first:** [start a free sandbox bank](https://openclear-sandbox.fly.dev/ui/sandbox), your own bank in about a minute with sample checks, ACH and exceptions, to see what a plugin would extend.

You can extend OpenClear without changing its code, using a separately installed Python package: another payment network, an OCR or storage service, a scoring model, or an import format for your core's files.

A plugin declares an entry point in the `openclear.plugins` group:

```toml
[project.entry-points."openclear.plugins"]
rtp = "openclear_rtp:plugin"
```

The object it points to has these:

- a `name`;
- a `version`;
- `plugin_api`, the range of the plugin API version it supports, for example `(1, 1)`;
- `register(registry)`.

Installing a package isn't enough to turn it on. The operator also allows it by entry point name with `OPENCLEAR_PLUGINS=rtp,other`.

A plugin is skipped if it:

- isn't allowed;
- needs a different plugin API version;
- fails to load;
- claims a name that's already taken.

Each skipped plugin is reported in the log and under **Settings → Plugins**, and the app still starts. OpenClear's own networks, OCR providers, email and SMS, storage, scoring model and import formats register the same way, from `plugins/builtin.py`.

Plugins run inside OpenClear with its full access. They're trusted code, so install only plugins you'd trust as much as OpenClear itself.

A plugin loaded into OpenClear is part of the same program, so it's AGPL-3.0 too. A closed system should integrate through the API and webhooks instead.

A plugin's import formats and decision file layouts are off for each bank until its admin turns them on under **Settings → Plugins**. To make a plugin required, set `OPENCLEAR_PLUGINS_REQUIRED`; OpenClear won't start if it doesn't load.

To write a plugin:

- read [docs/PLUGINS.md](docs/PLUGINS.md);
- copy [examples/openclear-plugin-example](examples/openclear-plugin-example).

The plan is in [PLUGIN_PLAN.md](PLUGIN_PLAN.md).

## Upgrade and downgrade support

- **Upgrading is always supported**, from any prior version straight to the latest —
  `alembic upgrade head` must work regardless of how old your starting version is. This is
  enforced by `tests/test_migrations/test_upgrade_downgrade_policy.py::
  test_full_upgrade_from_base_succeeds`, run as part of the normal test suite.
- **Downgrading is only supported up to 2 minor versions back**, and never across a major
  version boundary — a migration introduced at a major version bump is allowed to have an
  irreversible `downgrade()` (raising, or a documented no-op), reserving room for
  genuinely breaking changes to exactly that boundary.

`migrations/version_history.py` records which Alembic revision was `head` at each release
— `test_downgrade_two_minor_versions_supported` resolves "2 minor versions back" from
there and actually runs the downgrade against a scratch database. It skips cleanly (not
silently, not failing) whenever there isn't yet 2 minor versions of recorded history to
test against.

**Cutting a release**: bump the version in `pyproject.toml` and `src/openclear/main.py`
together, then add one new entry to `VERSION_HISTORY` in `migrations/version_history.py`
mapping the new version to the current Alembic head — only if that release actually added
a migration (a patch that doesn't touch the schema doesn't need an entry). Never edit or
remove a past entry.

## Web UI

Server-rendered (FastAPI + Jinja2, no Node/build step) under `/ui/*`, covering every
resource: accounts, issued items, stop payments, paid items, check images (upload +
OCR status), ACH authorizations/transactions, the exceptions review queue
(recommend/decide), admin ML screens, users/security groups, per-tenant branding
settings, the immutable action log, and WebAuthn security-key management.

**Settings** is one control panel (`/ui/settings`, `web/control_panel.py`): tiles grouped
into sections (Organization, Security & access, Decisions & rules, Integrations, Data &
compliance), each showing how its setting stands, with a search box. A person sees only the
tiles their permissions allow; a business customer's own users get their customer's panel
(reminders, banner, billing statement). Small settings open in a dialog and have their own
URL (`/ui/settings/<key>`), which without JavaScript is a plain page; lists and workflows
open as pages, each with a way back to its tile. The old Admin page is part of it
(`/ui/admin` redirects there).

**Reminders before the cutoff** (Settings → Reminders): before each open exception's
decision deadline, everyone who can recommend or decide it gets one message, by email or
text per their notification preferences, saying how many items are due and when the first
is. The timing is minutes before the cutoff, by default 120 and 30
(`OPENCLEAR_REMINDER_OFFSETS_MINUTES`); each bank sets its own or turns them off, and each
business customer can override its bank's. They're sent by the disposition sweep
(`OPENCLEAR_ENABLE_DISPOSITION_SCHEDULER`).

It's a second presentation layer over the same `services/`/`auth/` code the JSON API
uses (`web/routers/*.py` call service functions directly — never the JSON API over
HTTP), authenticated via cookies instead of a bearer token: `web/deps.py::get_web_context`
reads an `access_token` cookie, `auth/deps.py::get_current_context` reads the
`Authorization` header — the two channels never read each other's credential. Moving to
cookies reintroduces CSRF risk the header-based API doesn't have, so every `/ui/*` POST
is guarded by a double-submit cookie token (`web/security.py`); UI gating checks the same
`ctx.permissions` set (resolved from the caller's security group) the API enforces,
exposed to templates as a `can(ctx, permission)` Jinja global — hiding a button is
cosmetic, the POST route's own permission check is what actually enforces it.

## Architecture at a glance

The full picture (and the reasoning behind it) is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

- `db/` — engine/session factory (one code path for all three backends), tenant context
- `domain/` — SQLAlchemy models (import `openclear.domain` to register every mapper — see
  its `__init__.py` docstring for why this matters)
- `networks/` — the pluggable per-payment-network layer (`check/`, `ach/`); each
  implements `networks.base.NetworkAdapter` and self-registers via
  `networks.registry.register_adapter()`. Adding a new network (e.g. RTP) means a
  plugin that registers its adapter (see [Plugins](#plugins)) — no changes to
  `exception_item`, `decision`, `ml/`, or the `/exceptions` API.
- `ocr/` — pluggable OCR (`OCRProvider` protocol; Tesseract is the default, cloud
  providers are stubbed behind optional extras)
- `ml/` — model training/scoring per network, fed by human pay/return decisions
  (`decision.features_json`). Each organization chooses the **shared model** (trained on
  every participating organization's decisions, run by the platform operator through
  `/api/v1/platform/ml/*` with a `shared_model`-scoped platform key) or a **bank-only model**
  (its own decisions only, seeded from a copy of the shared model when it switches, run by
  its own admins). Customer models sit on top of either. See `ml/predict.py` for which model
  scores an exception, `services/tenant_ml_service.py` for switching, and the admin "ML
  Scoring" docs for the details a bank sees.
- `plugins/` — the plugin registry and loader; `plugins/builtin.py` registers the built-ins
- `api/v1/` — FastAPI routers; `exceptions.py`/`decisions.py` are network-agnostic
- `workers/` — the scheduled jobs (ML retrain, dropbox import, notifications, disposition
  sweep, demo reset), run by an opt-in in-process APScheduler (`OPENCLEAR_ENABLE_ML_SCHEDULER`
  and friends) or an external cron/k8s CronJob calling the functions in `workers/tasks.py`.
  `workers/leader_lock.py` keeps each job to one instance at a time on Postgres
- `web/` — the server-rendered UI (see "Web UI" above); `templates/` and `static/` live
  inside the package so they ship with it wherever it's installed
- `scripts/launcher.py` — the one-click local setup/run script (stdlib-only until it
  re-execs itself under a freshly-created venv's own interpreter); `run_openclear.command`
  (macOS), `run_openclear.bat` (Windows), and `run_openclear.sh` (Linux) are thin
  platform-specific wrappers around it — all three just locate a Python interpreter and
  hand off to the same script

## License

Copyright (C) 2026 Chaffed

OpenClear is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version.

This means that if you run a modified version of OpenClear as a network service, you must
make the modified source available to that service's users — see [LICENSE](LICENSE) for
the full text.

**Building an integration?** Start with a free developer sandbox (`sandbox/`, or `/ui/sandbox`
on a sandbox installation) and the [integration checklist](docs/guides/certification.md).

The client SDKs in [sdks/](sdks) are licensed **Apache-2.0** instead (each has its own `LICENSE`). A proprietary core or online banking platform can include them without any AGPL obligation.
