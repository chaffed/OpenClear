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

**Status: COMPLETE (2026-09-24, branch `scale-s1a`).** Added 2026-09-23 from S0's findings.

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

**Status: NOT STARTED.** Confirm each item with S0's `EXPLAIN ANALYZE` before and after.

- [ ] Indexes:
      - `paid_item (tenant_id, account_id, check_number)`, for the duplicate-paid check
        on every presented check (today only `account_id` is indexed, so it scans the
        account's whole paid history);
      - the columns `stop_payment` matching uses;
      - `exception_item (tenant_id, status, created_at)`, for the queue, counts and
        dashboard, and `(status, decision_deadline)` for the expired-disposition sweep;
      - `ach_transaction` on the columns the frequency-limit count filters on;
      - `notification (status, created_at)`, for dispatch.
- [ ] Customer data exports load every decision at the bank and then filter in Python
      (`services/data_export_service.py:84`). Join in SQL instead.
- [ ] The exceptions queue loads each row's source item separately. Fetch them in one
      query per network (`get_many`).
- [ ] Combine the dashboard's separate counts into a few grouped queries.
- [ ] **API list endpoints aren't paginated.** `GET /api/v1/exceptions` (and, to check,
      paid items, issued items and ACH) return every row at the bank in one response. Add
      `limit`/`cursor` parameters with a maximum page size. This changes the API, so
      document it in API.md.
- [ ] **Over-long text becomes a 500 on Postgres and SQL Server** (found in S0). Nothing
      checks string lengths, and SQLite doesn't enforce them, so it never showed. Add a
      length check in the form and service layer from the column sizes, plus a fallback
      handler that turns a database `DataError` into a normal form error.
- [ ] Per-request lookups (revoked sessions, membership, security group): measure only.
      Cache them only if S0 shows they matter.

## Phase S3 — A durable job queue and a worker process

**Status: NOT STARTED.** Decision D2.

**The problem:**
- OCR (CPU-heavy) and data exports run as FastAPI `BackgroundTasks` inside the web
  process, so they compete with page requests. A restart loses them, and an export can be
  left marked "running" for good.
- Scheduled jobs do every bank's work in one pass on one instance.

- [ ] A `job` table: type, tenant, payload, status, attempts, `run_after`, `locked_by`,
      heartbeat and error. Jobs are claimed with `SELECT … FOR UPDATE SKIP LOCKED` on
      Postgres, and there's a single worker on SQLite.
- [ ] A worker entry point, `python -m openclear.workers.run`, runs as its own process or
      container. For a one-machine install (the launcher, the Fly demo) the web process
      can run an in-process worker thread instead, so small setups stay one process.
- [ ] Retries with backoff. A job whose heartbeat stops (its worker died) is picked up
      again. Failed jobs stay visible to the operator.
- [ ] Move OCR and data exports onto the queue. Exports must **stream** each entity to
      the archive in batches, rather than building it in memory, and drop the 50,000-row
      cap. S0 found that a bank with 100,000 issued checks can't export at all, and that
      raising the cap costs about 600 MB of memory per 100,000 checks.
- [ ] Scheduled jobs *enqueue* work instead of doing it: one dropbox scan per bank, and
      one retrain per model slot. That spreads the work across workers.
- [ ] Docs: README deployment sections, and a note in docs/ARCHITECTURE.md
      "Background work".

## Phase S4 — Bulk uploads as background jobs, batched

**Status: NOT STARTED.** Needs S3. Decision D4.

**The problem:** every row runs about 6–8 queries, a commit, ML scoring and a
notification, all inside the upload request. Files are also read fully into memory.

- [ ] An upload stores the file (already done: `bulk_upload_storage_dir`), creates the
      `bulk_upload_file` record with a status and progress counts, and enqueues a job. The
      page shows progress and the per-row results when they're done. The file is parsed
      and control totals are checked first (a bad file is still rejected whole).
- [ ] Batch the matching: for each chunk of about 1,000 rows, load the issued items,
      stops and existing paid items for the chunk's accounts in a few queries, not per
      row. Commit per chunk, with a savepoint per row, so one bad row still fails on its
      own.
- [ ] Score each chunk's new exceptions in one model call. Send notifications as one
      summary per upload rather than one per item (decision D5).
- [ ] Read CSV and NACHA files as a stream. For X9.37, stream records and write each
      image to storage as it's read, rather than holding the whole file in memory.
- [ ] The API bulk endpoints (`/api/v1/paid-items/bulk`, ACH, issued items) stay
      synchronous up to a row cap. Larger requests get a job id to poll.
- [ ] Check the dropbox import follows the same path.
- [ ] Tests: results identical to today for the existing fixtures; a 50,000-row timing
      recorded against S0's baseline.

## Phase S5 — ML training at scale

**Status: NOT STARTED.** Needs S3. Decision D3.

- [ ] A training window, `ml_training_window_days` (recommended default 365), plus a row
      cap. Load only the columns training needs (`features_json`, outcome, date) and
      stream them, instead of whole `Decision` objects.
- [ ] One job per model slot, via the queue. `retrain_job` only decides which slots need
      retraining.
- [ ] Put a size limit (LRU) on each process's loaded-model cache: one model per
      customer adds up.
- [ ] Record training time and row counts in `metrics_json`, so growth is visible.

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
