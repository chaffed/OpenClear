# OpenClear scalability plan

Started 2026-09-23, after release 1.5.0. This plan is based on reading the code; nothing
has been load-tested yet, so Phase S0 measures first. Each phase gets its own branch, a
progress log below (committed after each step), and a full test run before merging.
Nothing is pushed or deployed without asking.

**Where things stand:** one app process on one database handles a few banks at moderate
volume. The first limits are:
1. the audit log's hash chain can fork when two writes for one bank happen at once, which
   is a correctness bug, not just slowness;
2. bulk uploads do all their work inside the web request;
3. indexes are missing on the busiest lookups;
4. ML training loads every decision into memory on one instance;
5. OCR and exports run inside web processes;
6. files, and the rate limiter, are local to each instance.

**Targets** (to confirm; see Decisions): per bank, 100,000 presented items a day, and a
50,000-row file loaded in under 5 minutes; 50 banks on one installation; the exceptions
queue and dashboard under 500 ms (p95) with 1 million exceptions in the database.

## Phases at a glance

| Phase | What | Size | Why this order |
|-------|------|------|----------------|
| S0 | Green CI, a Postgres test run, a volume generator and baseline timings | M | Everything else is measured against it |
| S1 | Audit log chain safe under concurrency | S | A correctness bug; gets worse with load |
| S1a | Postgres row-level security working end to end (jobs, organization switching) | M | Found in S0: needed before a restricted database role can be used |
| S2 | Indexes and query fixes | S–M | Cheap, and a large win |
| S3 | A durable job queue and a worker process | M–L | S4, S5 and the OCR/export moves depend on it |
| S4 | Bulk uploads as background jobs, batched | M | The first thing a real bank hits |
| S5 | ML training at scale | M | Grows with every bank and customer |
| S6 | Shared storage and a shared rate limit for several instances | M | Needed to run more than one instance |
| S7 | Data growth: retention, connection pools, partitioning if needed | S–M | Only as S0's numbers show a need |

---

## Phase S0 — Measure, and test on Postgres

**Status: COMPLETE (2026-09-23, branch `scale-s0`, merged to `main` locally, not pushed).**
Docker (Colima) was used locally to reproduce CI on Linux and to run Postgres 16.
Full suites: SQLite on macOS (Python 3.13) 1,144 passed, 5 skipped; Postgres 16 1,148
passed, 1 skipped; Linux Python 3.11 container (as CI) 1,144 passed, 5 skipped.

- [x] **CI green.** It had failed since before 1.5.0 because of 25 tests, all test-only
      problems, reproduced in a `python:3.11-slim` container with the workflow's packages:
      - 20 X9.37 tests built fixtures as dictionaries keyed by `slice`, and slices are only
        hashable from Python 3.12. They now pass `(slice, value)` pairs.
      - 5 OCR tests drew the check image in Helvetica from a macOS system path. On Linux
        that fell back to Pillow's tiny bitmap font, which Tesseract misreads. They now use
        Pillow's bundled scalable font, the same on every OS.
- [x] **Postgres in CI and locally.** `OPENCLEAR_TEST_DATABASE_URL` switches
      `tests/conftest.py` to Postgres: the schema is built once with `alembic upgrade head`,
      and each test starts from truncated tables. There's a new `test-postgres` job in
      `.github/workflows/tests.yml`. Running it found these **real bugs**, all fixed:
      1. **A fresh Postgres database couldn't be migrated.** `23275a18351d` dropped
         `user.tenant_id` while the RLS policy on it still existed.
      2. **Every authenticated API request failed on Postgres.** `SET LOCAL
         app.current_tenant_id = :tid` is invalid SQL with a bound parameter under
         psycopg 3. It's now `set_config(..., true)`.
      3. **Under enforced RLS, a reused pooled connection raised errors instead of seeing
         no rows.** After a transaction the setting reads as `''`, and `''::uuid` fails.
         Migration `e5a1c7d3b9f2` rebuilds all 24 policies with `NULLIF(..., '')`.
      4. **Under enforced RLS, nobody could log in.** Login, and the token check on every
         request, read memberships before a tenant was set. Also, the setting was lost at
         every mid-request commit. `db/tenancy.py::bind_session_to_tenant` now records the
         tenant on the session and re-applies it at the start of every transaction. Login
         binds it once the bank is known, and the token check binds it before any lookup.
      5. The retrain cooldown could go negative when the database clock ran slightly
         ahead of the app's. Elapsed time is now clamped at zero.
      `tests/test_postgres/test_rls_enforced.py` runs the app as a non-superuser role with
      two banks' data present. It checks that the API and web UI work, see only their own
      bank, and can't write another bank's rows. Scheduled jobs and switching organization
      still don't work under enforced RLS: see the new **Phase S1a**.
      One Postgres-only test failure was test data: a >64-character customer number. That
      exposed a real gap (over-long text gives a 500 on Postgres), now listed in S2.
- [x] **Volume generator:** `scripts/perf/seed_volume.py` (bulk inserts; 5 banks × 100,000
      checks in 44 s).
- [x] **Baseline timings:** `scripts/perf/measure.py`, results below.

### Baseline (2026-09-23)

Postgres 16 in Colima (2 vCPU, 4 GB, arm64) on a Mac, app on the host. Data: 5 banks,
500,000 presented checks, 500,000 issued checks, 14,957 exceptions (13,458 decided), 100,000
ACH debits, 2,000 audit entries per bank. Timed as perf-bank-0's admin.

| Area | What | Time | Notes |
|------|------|------|-------|
| Pages | Dashboard `/ui/` | 18 ms | median of 5 |
| Pages | Exceptions queue (needs attention / all / page 50) | 26 / 24 / 24 ms | |
| Pages | Paid items (page 1 / page 200), issued items | 22 / 28 / 23 ms | |
| Pages | Audit log | 9 ms | |
| API | `GET /api/v1/exceptions` | 42 ms | **all 2,951 rows in one 1 MB response** |
| Query | Duplicate-paid check (runs for every presented check) | 1.0 ms | bitmap scan over the account's paid history |
| Query | Exceptions queue, first page | 0.3 ms | |
| Query | Expired-disposition sweep | 1.0 ms | sequential scan of `exception_item` |
| Audit | `verify_chain`, 2,000 entries | 93 ms | about 47 µs per entry, linear |
| ML | Retrain the shared check model, 13,458 decisions | 0.96 s | |
| Export | Full bank export | **failed** | 100,000 issued items is over the 50,000-row cap |
| Export | Same, with the cap raised | 3.7 s | **600 MB peak memory**, which grows with the data |
| Ingestion | 1,000 issued checks (bulk service) | 1.6 s | 634 rows/s |
| Ingestion | 2,000 presented checks, half raising exceptions | 14.3 s | **140 rows/s: 50,000 rows ≈ 6 minutes** |

What this changes in the plan:
- **Pages are fine at this size.** They're paginated and the queries are cheap. S2's
  indexes matter more as history grows: the duplicate check and the sweep scan more
  rows every day.
- **Bulk ingestion is the main bottleneck.** S4 (background jobs and batching) is the
  most valuable piece of work.
- **Exports stop working for a bank past 50,000 rows** of any kind. At the target
  volume, that's after one day. S3 must stream exports to disk and drop the cap.
- **The API returns whole tables** (S2).

## Phase S1 — Audit log chain safe under concurrency

**Status: COMPLETE (2026-09-24, branch `scale-s1`, merged to `main` locally).** Full suites: SQLite 1,148 passed, 6 skipped; Postgres 1,153 passed, 1 skipped.

**The problem:** each new entry read the bank's latest entry (by timestamp) with no lock
and chained onto it. Two requests at one bank at the same moment could both chain onto the
same entry, and `verify_chain` would then report tampering that never happened.
Timestamps could also tie, making the order ambiguous.

- [x] A per-bank `sequence` (`BIGINT`, unique on `(tenant_id, sequence)`). Migration
      `f7b2d4e6a8c1` numbers existing entries by following their chain links, not
      timestamps. It isn't a signed field, so existing signatures still verify.
- [x] Writing an entry (`services/audit_log_service.py::record_action`):
      - on Postgres, writers for a bank queue on `pg_advisory_xact_lock`;
      - on every database, the insert runs in a savepoint. A writer that loses the race
        on the unique number re-reads and retries: up to 10 attempts, with a short random
        back-off. The request's own changes are kept.
- [x] `verify_chain` walks by `sequence` in batches of 1,000 and releases each batch, so
      memory stays flat. The audit log page orders by `sequence`.
- [x] Tests:
      - a forced race on any database (a competitor takes the number between read and
        insert; the writer retries and the chain stays whole);
      - per-bank numbering, and walking across batch boundaries;
      - the migration's link-following order with tied timestamps;
      - on Postgres, 8 threads × 5 entries against one bank: no errors, sequence 1–40,
        and a valid chain.
- Probe (not kept as a test): the same 8 threads with the Postgres lock switched off. With
  5 attempts and no back-off, 5 writers gave up with a clean unique-constraint error; the
  chain still never forked. With the back-off added, all 40 succeed with about 15
  retries. That's the SQL Server case, since it has no equivalent lock here.
- Deferred: starting verification from a stored checkpoint. At the S0 baseline
  (47 µs per entry) a million entries verifies in under a minute, so it isn't needed yet.
  It's in S7 along with archiving.
- Also fixed: the README described audit entries as HMAC-signed; they're ECDSA-signed
  with a dedicated key pair.

## Phase S1a — Postgres row-level security, end to end

**Status: COMPLETE (2026-09-24, branch `scale-s1a`, merged to `main` locally).** Full suites: SQLite 1,148 passed, 12 skipped; Postgres 1,159 passed, 1 skipped. Added 2026-09-23 from S0's findings.

S0 made web and API requests work under enforced RLS. The code that works across banks
still broke, and now doesn't:

- [x] **Scheduled jobs** bind each unit of work to its bank:
      - the disposition sweep binds per exception. Before this it read "no setting" and
        left items open, and an ACH return's update to `ach_transaction` would have matched
        no rows;
      - the dropbox import binds per bank (in `scan_and_import_tenant`, so the CLI is
        covered too);
      - retraining a customer model finds the customer's bank from `exception_item`, not the
        RLS'd `customer` table, which would otherwise have saved the model with no bank.
- [x] **Switching organization.** A signed-in session also sets `app.current_user_id`, and
      migration `a1d9e3f5b7c2` lets the membership policy show the user's own rows in any
      bank (writes stay bank-bound through `WITH CHECK`). `db/tenancy.py::acting_as_tenant`
      reads a member bank's details (a customer name, WebAuthn keys) temporarily, then
      restores the binding.
- [x] **Other cross-bank paths:**
      - the platform usage report reads each bank while bound to it;
      - creating a bank binds the new bank for its seed rows;
      - SSO, WebAuthn login, exports, OCR and the demo reset were already bound;
      - the platform ML endpoints only read tables without RLS.
- [x] `tests/test_postgres/` (shared restricted-role fixtures in its `conftest.py`) covers:
      switching organization, the check and ACH sweeps, the dropbox import across two
      banks, the usage report, and creating a bank then signing in. With the S1a code
      changes reverted, all 6 new tests fail.
- [x] README now recommends a restricted app role, with the grants, instead of telling
      Postgres deployments to bypass RLS.

## Phase S2 — Indexes and query fixes

**Status: COMPLETE (2026-09-26, branch `scale-s2`, merged to `main` locally).** Full suites:
SQLite 1,160 passed, 14 skipped; Postgres 1,173 passed, 1 skipped; Linux / Python 3.11 1,159
passed plus one flaky ML test, fixed (ties in decision time now break by id) and then
passing in 6 of 6 runs.

- [x] Indexes (migration `b2e4f6a8c0d3`, also declared on the models):
      - `paid_item (tenant_id, account_id, check_number)`;
      - `stop_payment (tenant_id, account_id, check_number)`;
      - `exception_item (tenant_id, status, created_at)` and `(status, decision_deadline)`;
      - `ach_transaction (tenant_id, account_id, originator_id, effective_date)`;
      - `ach_authorization_rule (tenant_id, account_id, originator_id)`;
      - `notification (status, created_at)`.
      On a large existing Postgres database, run the upgrade in a quiet window: plain
      `CREATE INDEX` blocks writes to those tables while it runs.
- [x] The ACH frequency-limit check counts in SQL. It used to fetch every matching row
      and count them in Python.
- [x] Customer data exports filter decisions with a join, not in Python.
- [x] The exceptions and approvals queues load each page's source items with one query
      per network (`NetworkAdapter.load_source_items`), not one per row.
- [x] **API list endpoints are paged:** `limit` (default 500, maximum 1,000) and `offset`,
      oldest first (`created_at`, then `id`), with `X-Total-Count` and
      `Link: rel="next"` (`api/v1/pagination.py`; documented in API.md). This is a
      behavior change for clients that read more than 500 rows in one call.
- [x] **Over-long text:** a `DataError` is a form message (`web/form_errors.py`) or, as a
      backstop, a 400 page or JSON response. Tests are in `tests/test_postgres/`, since
      SQLite doesn't enforce lengths.
- Not changed, because the S0 baseline showed no need: the dashboard's counts (18 ms),
  and the per-request session and permission lookups.

After (same data plus 2,000 ingested checks; `EXPLAIN ANALYZE`):

| Query | Before | After |
|---|---|---|
| Duplicate-paid check (every presented check) | 1.04 ms, bitmap scan of the account's history | **0.07 ms**, index scan |
| Expired-disposition sweep | 0.97 ms, sequential scan | **0.02 ms**, index scan |
| `GET /api/v1/exceptions` | 42 ms, all 2,951 rows (1 MB) | 24 ms, one page of 500 (0.2 MB) |

Re-measured on an idle machine (2026-09-26, same data plus the checks ingested by earlier
runs):

| What | S0 baseline | After S2 |
|---|---|---|
| Exceptions queue (needs attention) | 26 ms | 11 ms |
| Paid items page 1 / page 200 | 22 / 28 ms | 24 / 32 ms (unchanged) |
| Retrain the shared model | 0.96 s | 0.97 s (unchanged) |
| Bulk load, 2,000 presented checks | 14.3 s (140 rows/s) | 8.8 s (226 rows/s) |
| 50,000-row file, projected | ≈ 6.0 min | ≈ 3.7 min |

The bulk-load gain comes from the duplicate-paid, stop-payment and ACH frequency indexes.
It's still well short of the D1 target (50,000 rows in under 5 minutes *with headroom*),
because each row still does its own queries and commit. That's S4.

## Phase S3 — A durable job queue and a worker process

**Status: COMPLETE (2026-09-28, branch `scale-s3`, merged to `main` locally).** Decision D2:
a job table in the existing database (no Redis/Celery).

Full suites: SQLite 1,174 passed, 15 skipped; Postgres 1,188 passed, 1 skipped; Linux / Python
3.11 1,173 passed plus one flaky test, fixed. The in-process thread test had shared the
suite's single in-memory SQLite connection with a worker thread; it now uses its own
file-based database, and passed 8 of 8 on Linux under load. Checked in the running app,
not just in tests:
- **in_process (default):** an API upload returned at once with OCR `pending`, and the
  worker thread completed it 0.2 s later, with the payee and amount read correctly;
- **external:** OCR stayed `pending` until `openclear-worker --once` ran the job.

- [x] A `job` table (migration `c4f6a8b0d2e5`): kind, bank, JSON payload, dedupe key,
      status, attempts/max, `run_after`, `locked_by`, heartbeat, last error, timestamps.
      No RLS on it (workers see every bank's jobs), but handlers run with their session
      bound to the job's bank.
- [x] The queue (`workers/jobs.py`):
      - `enqueue` joins the caller's transaction, so a job never runs against rolled-back
        data, and a dedupe key skips duplicates that are still waiting;
      - claiming is a conditional update, plus `SKIP LOCKED` on Postgres and SQL Server;
      - a heartbeat thread runs during each job;
      - failures retry with exponential back-off (30 s doubling, 1 h cap, jittered) up to
        `job_max_attempts` (5), then stay `failed` with the error;
      - a job whose heartbeat is older than `job_stale_after_seconds` (300) is queued again.
- [x] Worker modes (`worker_mode`):
      - `in_process` (default): a worker thread in each web process, started from the
        app's lifespan, so the launcher, the Docker image and the Fly demo need nothing new;
      - `external`: the `openclear-worker` command (`workers/run.py`; `--once` for cron),
        which stops cleanly on SIGTERM;
      - `inline`: the test suite.
- [x] **Moved onto the queue:**
      - OCR, for web and API uploads, bulk ZIP uploads and reprocessing;
      - data exports. Exports now **stream** each table into the ZIP in batches of 1,000,
        output byte-identical to before. **The 50,000-row cap is gone**, and the default
        time limit went from 5 minutes to an hour.
- [x] **Scheduled jobs queue per-unit work:** `dropbox_import_job` queues one scan per
      bank, and `retrain_job` one retrain per model slot (bound to the slot's bank), both
      deduplicated. The sweep, notification sending and the demo reset are quick and still
      run directly.
- [x] **Operator visibility:** `/api/v1/platform/jobs` (`summary`, a paged list filtered by
      status and kind, and `retry` for a failed job) with a new `operations` platform-key
      scope.
- [x] Tests:
      - `tests/test_auth/test_job_queue.py` covers success, binding to the bank, dedupe,
        not-yet-due jobs, back-off then giving up, stale recovery with and without attempts
        left, exclusive claims, unknown kinds, and the in-process thread;
      - `tests/test_postgres/test_job_queue_concurrency.py` runs 4 workers × 40 jobs, each
        exactly once;
      - the platform jobs API, including scope checks;
      - export streaming across batches;
      - the existing OCR, export, dropbox, retrain and RLS tests, which now go through the
        queue.
- [x] Docs: README "Background jobs" and "Running more than one instance", API.md,
      ARCHITECTURE.md, CHANGELOG.

## Phase S4 — Bulk uploads as background jobs, batched

**Status: COMPLETE (2026-10-08, branch `scale-s4`, merged to `main` locally).** Decisions D4
(1,000-row API cap) and D5 (one summary notification per upload).

Full suites: SQLite 1,215 passed, 15 skipped; Postgres 1,230 passed; Linux / Python 3.11
1,210 passed, 20 skipped.

- [x] An upload stores the file, records it with a status and progress counts
      (migration `a8c0e2f4b6d9`), checks it parses (a bad file is still rejected whole, on
      the form), and queues a `bulk_upload.import` job (`services/bulk_import_service.py`).
      The upload's page (`/ui/bulk-uploads/<id>`) shows progress and refreshes itself, then
      the result: failed rows with reasons, and a CSV of every row
      (`bulk_upload_row_result`). Covers accounts, issued items, presented checks, ACH
      (delimited and NACHA) and check images (zip and X9.37). Users and fraud-training
      labels stay in the request: they're small and need an answer on the spot.
- [x] Chunks of `bulk_import_chunk_rows` (1,000): each row in a savepoint, one commit per
      chunk together with its row results, audit entries, back-out records and
      `processed_rows`, so a retried job resumes without importing a row twice. **Found
      while testing:** on SQLite a savepoint opened before any write commits on release,
      so each row was committing alone; each chunk now opens with a write.
- [x] Audit entries are chained a chunk at a time (`audit_log_service.record_actions`:
      one lock and one read of the latest entry per chunk). Notifications: one summary
      per reviewer per upload, counted per customer.
- [~] **Batched matching** (pre-loading issued items, stops and paid history per chunk):
      **not done.** Measured below, the D1 target is met without it; the matching rules
      stay row by row and unchanged. Revisit if a real bank's volumes need it. Scoring
      stays per exception (models are cached).
- [x] Files aren't read into memory: uploads stream to disk, hashed and signed as written
      (`sign_digest`); X9.37 is read through an mmap with images sliced per check; zip
      images are read as each row needs them. Delimited and NACHA files are parsed whole
      (a 50,000-row CSV is a few MB).
- [x] The API `/bulk` endpoints stay synchronous up to 1,000 items; more answers `202`
      with an upload to poll (`/api/v1/bulk-uploads/{id}` and `/results`).
- [x] The dropbox import goes through the same job.
- [x] Tests: `tests/test_bulk_import/test_bulk_import_service.py` (chunks, an isolated bad
      row, resume after a crash without duplicates, failure on the last attempt, a job the
      queue gave up on, rejection, the summary notification, streamed signatures, lazy
      zip and X9.37 images, the API's 202 and status endpoints, the progress page); the
      existing bulk tests pass unchanged apart from following the redirect.
- **Also fixed:** the upload page failed for presented-check and fraud-training uploads
  (their kinds were missing from its permission map); a missing ML model file made every
  exception row fail (now unscored, as a failed integrity check already was).

**Timing** (perf database, perf-bank-0, Postgres in Colima; the laptop was busy, with
Google Drive syncing, so runs varied up to 3×; best of three alternating runs, 1,000 rows,
half raising exceptions):

| Path | Rows/s | 50,000 rows |
|---|---|---|
| Before (in the request, a commit per row) | 103 | ≈ 8 min |
| S4 background import | 243 | ≈ 3.4 min |

Storing, signing and checking a 2,000-row file in the request takes about 50 ms.

## Phase S5 — ML training at scale

**Status: COMPLETE (2026-10-08, branch `scale-s5`).** Decision D3 (365 days, configurable).

- [x] A training window, `ml_training_window_days` (365; 0 for all history), and a row
      cap, `ml_training_max_rows` (250,000, the newest). Known-fraud training examples are
      kept whatever their age. Training reads only `features_json` and the outcome, in
      batches of 5,000, instead of whole `Decision` objects.
- [x] One job per model slot via the queue (done in S3). `retrain_job` now counts with SQL
      instead of loading every decision, and compares against the eligible count the last
      run recorded, so a capped slot isn't retrained every night.
- [x] The loaded-model cache is an LRU of `ml_model_cache_size` (256) per process.
- [x] `metrics_json["training"]`: rows used, eligible, capped, window, cap, load and total
      seconds.
- [x] Tests: `tests/test_ml/test_training_at_scale.py` (window with the fraud-example
      exception, the cap keeping the newest rows, a capped run not looking due again, LRU
      eviction and refresh).

Measured on the perf database (13,375 shared-model decisions): loading the training data
peaks at 14 MB of Python memory, against 41 MB loading `Decision` objects as before, and
is a little faster (0.31 s against 0.45 s).

## Phase S6 — Shared storage and shared limits for several instances

**Status: NOT STARTED.** Decisions D6 and D7.

- [ ] A storage backend interface (local disk, or S3-compatible object storage) for check
      images, logos, bulk uploads, exports and ML artifacts. Models keep their SHA-256 check.
      Existing paths keep working, and there's a migration script to move files.
- [ ] A pluggable rate limiter backend: per process (today) or shared. Keep the proxy/WAF
      advice in the README.
- [ ] Connection pool settings in config (`pool_size`, `max_overflow`), with sizing advice
      for N instances plus workers. There are none today, so each process gets
      SQLAlchemy's default of 5 connections plus 10 overflow. Add a note on PgBouncer.
- [ ] Update README "Running more than one instance" to say what's now shared.

## Phase S7 — Data growth

**Status: NOT STARTED.** Only as far as S0's numbers justify.

- [ ] A retention policy per table (paid items, ACH transactions, notifications, audit
      log), as settings. The audit log is never deleted; old entries can be archived with
      a verified checkpoint.
- [ ] If S0 shows it's needed: monthly Postgres partitioning of `paid_item`,
      `ach_transaction` and `audit_log_entry`.
- [ ] A scale test in CI, kept small enough to run on every push, that fails on a large
      regression in the S0 timings.

---

## Decisions

Recommended options are marked. Under the standing instruction, I'll go with the
recommendations unless you say otherwise.

| # | Question | Recommendation | Alternatives |
|---|----------|----------------|--------------|
| D1 | Scale targets | **Per bank: 100k items/day and a 50k-row file in < 5 min; 50 banks; queue p95 < 500 ms at 1M exceptions** | Your real numbers, if you have a bank in mind |
| D2 | Job queue technology | **A database-backed job table** (no new infrastructure; Postgres `SKIP LOCKED`) | Redis with RQ or Celery (more moving parts, higher ceiling) |
| D3 | ML training window | **365 days, configurable** | All history (today); 180 days |
| D4 | API bulk row cap before switching to a job | **1,000 rows** | 5,000 |
| D5 | Notifications for bulk uploads | **One summary per upload** | One per exception (today) |
| D6 | Object storage | **S3-compatible first** (also covers MinIO, R2 and others); Azure Blob later | Azure first |
| D7 | Shared rate limit | **Pluggable: per process by default, Redis optional**; keep the proxy/WAF advice | Postgres-backed counters |

## Progress log

(Newest last. Each entry is committed.)
- 2026-09-23 S0 complete. See the S0 section for what was fixed and the baseline. New
  Phase S1a added; S2 and S3 extended.
- 2026-09-24 S1 complete (audit chain safe under concurrency).
- 2026-09-24 S1a complete (Postgres RLS end to end).
- 2026-09-25 Renamed the project to OpenClear (branch `rename-openclear`, merged).
- 2026-09-26 S2 complete. Also fixed: the retrain cooldown now uses the database clock, and the training holdout split is reproducible.
- 2026-09-28 S3 complete (job queue and worker; streaming exports). X9.37 field verification deferred until a bank implements.
- 2026-10-08 Billing (BILLING_PLAN.md) and release 1.6.0, pushed. S4 complete (bulk imports in the background).
- 2026-10-08 S5 complete (training window, row cap, leaner loading, model cache limit).
