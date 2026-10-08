# Changelog

## Unreleased

### Scalability (SCALE_PLAN.md S4): bulk files import in the background

- **Uploads no longer import inside the web request.** Accounts, issued items, presented
  checks, ACH transactions and check images are stored, checked, and then imported by a
  background job. The upload's page shows progress, then the result: rows that failed
  and why, plus a CSV of every row. A file that can't be parsed is still rejected on the
  form with nothing imported. The dropbox import uses the same path.
- **Faster and safer imports:** 1,000 rows commit at a time, with each row in a savepoint,
  and audit entries are written a chunk at a time. On the test laptop a 50,000-row file
  now takes about 3.5 minutes instead of about 8. An interrupted import resumes where it
  stopped without importing any row twice.
- **One notification per upload:** reviewers get a single "N new exceptions" summary
  instead of one email or text per exception.
- **API:** a `/bulk` call with more than 1,000 items (`OPENCLEAR_BULK_API_SYNC_MAX_ROWS`)
  now answers `202` with an upload to poll at `/api/v1/bulk-uploads/{id}` (and
  `/results`). **Behavior change** for clients that send more than 1,000 items in one call.
- **Large files aren't held in memory:** uploads stream to disk, X9.37 cash letters are
  read through a memory map, and zip images are read as needed.
- **Fixes:**
  - the upload detail page failed for presented-check and fraud-training uploads;
  - a missing or unreadable ML model file made every exception row in an upload fail. It
    now leaves those exceptions unscored, as a failed integrity check already did.
- **Upgrading:** run `alembic upgrade head` (new columns on `bulk_upload_file`, new table
  `bulk_upload_row_result`).

## 1.6.0 — 2026-10-08

PosPay is now **OpenClear**. This release adds billing (account analysis and hard charge,
with camt.086 export), a durable background job queue, paged API lists, and Postgres
fixes for running at scale.

### Upgrading from 1.5.0

- Run `alembic upgrade head` (the Docker image does this on startup). On a large Postgres
  database, run it in a quiet window: the new indexes block writes to their tables while
  they build.
- **API clients:** list endpoints now return at most 500 rows per call (1,000 with
  `limit`). Follow the `Link` header to read the rest.
- Your `POSPAY_*` settings keep working; see the rename notes below.
- Downgrading to 1.5.0 is supported (`alembic downgrade c3e8f1a5d9b2`).

### Renamed: PosPay is now OpenClear

The project, its Python package (`openclear`), its settings (`OPENCLEAR_*`) and its launcher
(`run_openclear.command` / `.sh` / `.bat`) are renamed. **Existing installs keep working
without changes:**
- `POSPAY_*` settings are still read, from the environment and from `.env`. When both are
  set, `OPENCLEAR_*` wins. Rename them when convenient.
- A local `./pospay.db` is still used if there's no `./openclear.db`.
- The desktop launcher moves `.pospay-run/` to `.openclear-run/` on its first run (your
  database, keys and uploads come with it). It rebuilds its Python environment, which
  takes a minute.
- Platform API keys issued before the rename (`pospay_platform_...`) still work. New keys
  start `openclear_platform_`.
- A saved light/dark theme carries over.
- Kept as they are, because they identify existing deployments: the demo's Fly app and
  hostname (`pospay-demo`; WebAuthn keys are bound to it), its volume, and the Docker
  image's database path `/data/pospay.db`.
- If you import the package in your own scripts, change `pospay` to `openclear`.
- `docker-compose.yml` (development only) now names its Postgres user, database and
  volume `openclear`, so a local compose database starts fresh.

### Scalability (SCALE_PLAN.md S0, S1, S1a)

- **Postgres fixes:**
  - a fresh Postgres database can now be migrated;
  - authenticated API requests no longer fail on Postgres;
  - row-level security works end to end for a restricted app role. That covers login,
    requests, scheduled jobs, switching organization, usage metrics and creating a bank.
  - Connect the app as a role without `BYPASSRLS` (see README, "Postgres").
- **The audit log's hash chain is safe under concurrent writers:** a per-bank sequence
  number, a per-bank lock on Postgres, and a retry.
- **Tests:** the suite runs against Postgres in CI (`OPENCLEAR_TEST_DATABASE_URL`), and the
  CI failures on Linux / Python 3.11 are fixed.
- **Tools:** `scripts/perf/` has a volume generator and a timing script.
- **API list endpoints are paged** (S2): `limit` (default 500, at most 1,000) and
  `offset`, oldest first, with `X-Total-Count` and a `Link: rel="next"` header (see
  API.md). **Behavior change:** clients that read more than 500 rows from one call must
  now follow `Link`.
- **Faster hot paths** (S2): new indexes for the duplicate-paid check, stop-payment
  matching, the exceptions queue, the disposition sweep, the ACH frequency check and
  notification dispatch. The ACH frequency check counts in SQL. The queue pages load
  source items in one query per network, and customer exports filter decisions in SQL.
  On a large existing Postgres database, run the upgrade in a quiet window: building the
  indexes blocks writes to those tables while it runs.
- **Background jobs** (S3): OCR, data exports, dropbox scans and retraining are jobs in
  a durable queue (the `job` table) instead of work done inside the web request. They're
  retried with back-off, and picked up again if a worker dies. By default each web process
  runs a worker thread, so nothing changes for single-process installs. To size workers
  separately, set `OPENCLEAR_WORKER_MODE=external` and run `openclear-worker`. The platform
  operator can see and retry failed jobs at `/api/v1/platform/jobs`, with a platform key
  carrying the new `operations` scope.
- **Exports have no row limit** (S3): they stream to disk in batches. The
  `data_export_max_rows_per_entity` setting is gone, and the default time limit is now an
  hour instead of 5 minutes.
- **Over-long text** is now a form message (or a `400` from the API) instead of an error
  page on Postgres and SQL Server.

### New: billing (BILLING_PLAN.md)

- **A bank can bill its customers for positive pay.** The fee schedule draws on 24
  metrics: per customer, user, account, issued item or file, item evaluated, exception,
  item returned, stop payment, image, notification and more. Each fee has a unit price,
  optional step tiers, a monthly minimum and the bank's AFP Service Code. It's under
  Billing in the Organization menu and at `/api/v1/billing`.
- **Each customer is on account analysis, hard charge or exempt**, with a bank-wide
  default. The bank can also give a customer a discount, a negotiated price or a waiver
  on any fee.
- **Statements:** live for any period, with volumes per account, plus a PDF. Finalizing a
  month freezes it, and a replacement version can follow.
- **Exports** from a finalized month: the billing report (CSV), an analysis feed (CSV), a
  hard-charge posting file for the core (CSV), and ISO 20022 camt.086.
- **Customer users** with `billing:read` see their own statement.
- **Platform billing:** the platform operator can invoice each bank at
  `/api/v1/platform/billing`, with a platform key carrying the new `billing` scope.
- **Upgrading:** run `alembic upgrade head`. Existing Admin security groups get
  `billing:read` and `billing:manage`. Nothing is charged until a bank adds fees.

## 1.5.0 — 2026-09-23

A security, reliability and usability release. It fixes every finding from the September
2026 review ([EVALUATION.md](EVALUATION.md)). It also lets each bank choose which fraud
model scores its exceptions, and it adds new screens for deciding exceptions.

### Upgrading from 1.0.0

- Run `alembic upgrade head`. The Docker image does this on startup. The new migrations
  only add columns and fill in values for existing rows.
- **Existing banks stay on the shared fraud model**, with nothing to do. New banks start
  on it too, and can switch to a bank-only model after 90 days.
- **Model files are now fingerprinted.** The upgrade records each existing model file's
  SHA-256. A model whose file can't be found during the upgrade isn't loaded until it's
  retrained. Its exceptions go unscored in the meantime, and it's logged.
- **Several instances:** read "Running more than one instance" in the README first.
  Several instances against one database are supported on Postgres only, and they need a
  shared volume for files.
- The public demo organization now resets every hour, and some destructive actions are
  locked there.

### Security

- A customer-scoped user could export the whole bank's data. Exports are now limited to
  the caller's own customer.
- Changing a user's security group now takes effect on their next request, not when
  their token expires.
- Logging out, changing a password, and an admin's "sign out everywhere" now end sessions
  on the server.
- SSO issuer URLs can't reach private or internal addresses, and the OIDC `state` is
  verified.
- Uploaded SVG logos and bulk-upload downloads are served safely.
- Only the platform operator can train or activate the shared fraud model. Banks control
  only their own models. A bank's fraud-training examples reach the shared model only after
  the operator approves them.
- Model files are verified against their recorded SHA-256 before they're loaded.
- The development `docker-compose.yml` publishes Postgres on localhost only.

### New

- **Fraud model choice per bank:** each bank uses the shared model or its own model.
  Switching copies the shared model, so scoring never stops. New versions replace the
  active model only if they do at least as well on the same recent decisions. See
  Settings → Fraud scoring, and the platform operator API.
- **Exception review:** a detail page that shows the evidence (images, OCR, the matching
  issued item, which checks failed and why), with no default choice on the decision
  forms. The queue opens on items that need attention. There's also an Approvals queue for
  maker/checker review, with a count in the navigation.
- **Password lifecycle:** people can change their own password, admins can reset one, and
  a password change can be required at next login.
- **Sessions:** activity keeps a session alive, and the page warns 5 minutes before it
  would expire.
- A dashboard built around what needs doing.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how OpenClear fits together, and why.

### Fixes

- X9.37 files are checked against their bundle control totals as well as their cash
  letter and file totals. If any total doesn't match, the whole file is rejected.
- On Postgres, scheduled jobs run on one instance at a time.
- Errors show friendly pages and form messages instead of bare 500s or raw database text.
- Pages no longer scroll sideways on phones. The settings and admin pages were tidied.
