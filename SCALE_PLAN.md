# PosPay scalability plan

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
| S2 | Indexes and query fixes | S–M | Cheap, and a large win |
| S3 | A durable job queue and a worker process | M–L | S4, S5 and the OCR/export moves depend on it |
| S4 | Bulk uploads as background jobs, batched | M | The first thing a real bank hits |
| S5 | ML training at scale | M | Grows with every bank and customer |
| S6 | Shared storage and a shared rate limit for several instances | M | Needed to run more than one instance |
| S7 | Data growth: retention, connection pools, partitioning if needed | S–M | Only as S0's numbers show a need |

---

## Phase S0 — Measure, and test on Postgres

**Status: NOT STARTED.**

- [ ] **Get CI green.** GitHub Actions has failed on `main` since before 1.5.0 (runs
      31066858937 and 35898227517, both Python versions, in "Run tests"). The full suite
      passes locally on macOS with Python 3.13, so the cause is likely Linux-specific
      (Tesseract version, fonts, paths, timing). Reading the logs needs GitHub sign-in:
      run `gh auth login`, or paste the failing part of the log.
- [ ] **Postgres in CI.** Add a job with a `postgres` service container that runs
      `alembic upgrade head` and the test suite against Postgres. `tests/conftest.py`
      hard-codes in-memory SQLite, so it needs a `POSPAY_TEST_DATABASE_URL` switch, with
      per-test isolation by transaction rollback or a schema reset. That also finally
      exercises the RLS policies and `workers/leader_lock.py` for real.
- [ ] **Volume generator:** `scripts/perf/seed_volume.py` creates N banks, customers,
      accounts, issued items, paid items, ACH transactions, exceptions and decisions at a
      chosen scale (for example `--banks 50 --items-per-bank 200000`).
- [ ] **Baseline timings:** `scripts/perf/measure.py` times each hot path against that
      data on Postgres:
      - a bulk load of 10,000 and 50,000 paid items;
      - the exceptions queue and dashboard;
      - one ML retrain of the shared model, a bank model and a customer model;
      - a full data export;
      - `verify_chain`.
      It also runs `EXPLAIN ANALYZE` on the queue, duplicate-check and sweep queries, and
      the results go in this file. Every later phase is judged against these numbers.

## Phase S1 — Audit log chain safe under concurrency

**Status: NOT STARTED.**

**The problem:** `services/audit_log_service.py` reads the bank's latest entry by
`occurred_at` (`repositories/audit_log_repo.py:17`) with no lock, then inserts the next
entry chained to it. The app handles requests on several threads even in one process, so
two actions at one bank in the same instant can both chain onto the same entry, and
`verify_chain` then reports tampering that never happened. Ordering by timestamp is also
ambiguous when two entries share a timestamp.

- [ ] Add a per-bank `sequence` number (`BIGINT`, unique on `(tenant_id, sequence)`). The
      migration backfills it in the chain's current order.
- [ ] Writing an entry:
      - on Postgres, take `pg_advisory_xact_lock` on the bank first, so writers queue
        instead of colliding;
      - on every database, insert inside a savepoint and, if another writer took that
        sequence number, re-read the latest entry and retry (a few attempts).
      This way the business change and its audit entry still commit together.
- [ ] `verify_chain` walks entries by `sequence` in batches (constant memory), and can
      start from a stored, verified checkpoint rather than always from the first entry.
- [ ] Tests: concurrent writers (threads, on a file-based SQLite and on Postgres in CI)
      always produce one unbroken chain; the migration keeps existing chains valid.

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
- [ ] A worker entry point, `python -m pospay.workers.run`, runs as its own process or
      container. For a one-machine install (the launcher, the Fly demo) the web process
      can run an in-process worker thread instead, so small setups stay one process.
- [ ] Retries with backoff. A job whose heartbeat stops (its worker died) is picked up
      again. Failed jobs stay visible to the operator.
- [ ] Move OCR and data exports onto the queue.
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
