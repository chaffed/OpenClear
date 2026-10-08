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
- [Billing](#billing)
- [Authentication](#authentication)
- [Signing keys](#signing-keys)
- [Reverse proxy / WAF deployment](#reverse-proxy--waf-deployment)
- [Docker](#docker)
- [Demo tenant](#demo-tenant)
- [Postgres](#postgres)
- [MSSQL](#mssql)
- [Running more than one instance](#running-more-than-one-instance)
- [Background jobs](#background-jobs)
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
- **NACHA** (`bulk_import/nacha.py`, ACH only): a standard 94-character fixed-width ACH
  file. Extracts the batch header fields (company id/name, SEC code, effective date) and
  entry detail fields (DFI account number, amount, individual id, transaction code, trace
  number) needed to create transactions, and checks each batch's and the file's control
  totals (entry count, entry hash, debit and credit totals). A file whose totals don't add
  up is rejected whole.

Every row/entry carries its own account number (not a single account picked once for the
whole file) — resolved against your existing accounts, so one file can span multiple
accounts. Unmatched account numbers, and any other bad row, are reported individually
without failing the rest of the file.

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
- **X9.37 image cash letter** (`bulk_import/x937.py`): a real Check 21 cash letter file.
  **Lenient by design**, matching this app's existing NACHA parser's philosophy: supports
  the common ASCII, line-delimited "variable format" variant with embedded TIFF/JPEG
  images in Image View Data (Type 52) records; does not support the alternate
  fixed-length undelimited binary variant, EBCDIC encoding, return/adjustment cash
  letters, or credit reconcilement records. Bundle (Type 70), cash letter (Type 90) and
  file (Type 99) control totals are validated: a mismatched item count or dollar amount
  rejects the whole file. The control-record field positions follow the published
  layouts but haven't been checked against a real processor's file. A cash letter is read
  through a memory map, with each check's images read only as that check is imported, so
  a large file isn't held in memory; a zip's images are likewise read as each row needs
  them. Account number and check number come from each check detail record's On-Us
  and Auxiliary On-Us MICR fields respectively — a common real-world convention (the
  check's own serial number in Auxiliary On-Us, the payor's account number in On-Us), not
  a standard-mandated split, so a bank whose files use a different layout won't resolve
  correctly here; the ZIP+CSV format above is the explicit-column fallback for that case.

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

Not yet done, documented as an accepted v1 limitation: Postgres RLS policies on the six
customer-scoped tables enforce `tenant_id` only, not `customer_id` — the repository layer
above is the primary, tested enforcement point, same posture as tenant isolation's own RLS
today.

## Per-tenant branding

Each tenant can customize a logo, favicon, accent color, and display name from
`/ui/settings` (gated by a dedicated `tenant:manage` permission, not folded into
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

## Authentication

Username + password (bcrypt-hashed) issuing JWTs (`api/v1/auth.py`), with an optional
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

Every request gets a per-IP rate limit (`config.py::rate_limit_per_minute`, 120/minute by
default; `POST /ui/markdown-preview` has a stricter one of its own on top,
`markdown_preview_rate_limit_per_minute`, 30/minute by default) — in-memory and
per-process (`web/rate_limit.py`), matching `scripts/launcher.py`'s single uvicorn
process with no `workers=N`. A future multi-worker production launch would give each
worker its own counters, multiplying the effective limit by worker count — worth knowing
before assuming a configured limit is the actual limit under that kind of deployment (see
"Running more than one instance" below).

That per-IP limiting (and the signer IP recorded on a Written Statement of Unauthorized
Debit attestation, `web/routers/wsud.py`) both resolve "the caller's IP" via
`web/client_ip.py::get_client_ip`, which by default trusts nothing but the direct TCP
connection. Deployed behind a reverse proxy or WAF, every request's direct connection is
actually the proxy's own address — set `OPENCLEAR_TRUSTED_PROXY_COUNT` to the number of
proxy hops in front of the app (usually `1`) so it instead trusts exactly that many
entries from the *right* end of the `X-Forwarded-For` header — the hop your own proxy
chain appended, never a client-supplied value further left in the chain, which is
trivially spoofable. Leaving this at its default of `0` is the safe choice for a direct,
non-proxied deployment; setting it too high behind a thinner proxy chain than configured
would start trusting a header value the client itself controls.

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
- **Rate limits are per process** (`web/rate_limit.py`, see "Reverse proxy / WAF
  deployment" above). With N processes, a client can make up to N times the configured
  limit. Divide `OPENCLEAR_RATE_LIMIT_PER_MINUTE` by N, or enforce the real limit at your proxy
  or WAF. A shared limiter (for example, Redis) isn't built in.

- **Files are on local disk.** Check images, tenant logos, bulk-upload originals, data
  export archives and trained ML models are written under the `*_storage_dir` /
  `ml_artifact_dir` / `auto_import_dropbox_dir` paths in `config.py`, and only their paths
  go in the database. Every instance must see the same files, so point all of those at one
  shared volume. Otherwise an image uploaded through one instance, or a model trained on it,
  is missing on the others.
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
  `networks.registry.register_adapter()`. Adding a new network (e.g. RTP) means adding a
  new `networks/<code>/` package plus one import in `main.py` — no changes to
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
