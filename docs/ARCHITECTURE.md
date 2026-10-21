# OpenClear architecture

How OpenClear is put together, and why. It's written for engineers changing the code. For
setup and deployment see [README.md](../README.md), for the JSON API see
[API.md](../API.md), and for onboarding a bank or customer see
[RUNBOOK.md](../RUNBOOK.md).

Paths below are relative to `src/openclear/` unless they start with `tests/`, `migrations/`
or `docs/`.

## Contents

- [The big picture](#the-big-picture)
- [Tenancy: banks, customers, and isolation](#tenancy-banks-customers-and-isolation)
- [Data model](#data-model)
- [Payment networks: the adapter pattern](#payment-networks-the-adapter-pattern)
- [Matching rules](#matching-rules)
- [From exception to decision](#from-exception-to-decision)
- [ML pipeline](#ml-pipeline)
- [OCR](#ocr)
- [Bulk ingestion](#bulk-ingestion)
- [Authentication and sessions](#authentication-and-sessions)
- [Web UI vs. JSON API](#web-ui-vs-json-api)
- [Data exports](#data-exports)
- [Billing](#billing)
- [Background work and multiple instances](#background-work-and-multiple-instances)
- [Plugins](#plugins)
- [Known gaps](#known-gaps)

## The big picture

Positive pay: a bank's business customer tells the bank which checks it wrote (issued
items) and which ACH debits it expects (authorization rules). When a check is presented
or an ACH debit arrives, OpenClear compares it with what the customer said. If it matches,
it clears. If it doesn't, OpenClear raises an **exception**, and a person decides whether to
pay or return it before the settlement deadline.

```
 issued items / ACH rules ─┐
                           ▼
 presented item ──► network adapter ──► matched? ── yes ──► cleared
 (API, UI, bulk,     (evaluate rules)        │
  dropbox)                                   no
                                             ▼
                                    exception_item ──► ML score, notify, deadline
                                             │
                          person (maker/checker) or auto-disposition
                                             ▼
                                   decision (pay / return) ──► ML training data
```

Every decision a person makes becomes a labelled training example, so the fraud-risk
score improves as a bank uses the product.

Layers, top to bottom:

| Layer | Where | Job |
|-------|-------|-----|
| Web UI | `web/`, `templates/`, `static/` | Server-rendered pages, cookie auth, CSRF |
| JSON API | `api/v1/` | Bearer-token API for integrations ([API.md](../API.md)) |
| Services | `services/` | Business operations; both front ends call these |
| Networks | `networks/` | Per-network ingestion, rules, features |
| ML | `ml/` | Training, model registry, scoring |
| Repositories | `repositories/` | Tenant- and customer-scoped data access |
| Domain | `domain/` | SQLAlchemy models |
| DB | `db/`, `migrations/` | Engine, sessions, Alembic migrations |
| Workers | `workers/` | Scheduled jobs |

## Tenancy: banks, customers, and isolation

A **tenant** is a bank. A **customer** is one of the bank's business clients
(`domain/customer.py`). A **user** is a global login identity (`domain/user.py`), and a
**tenant membership** (`domain/tenant_membership.py`) attaches a user to one bank with a
security group and, optionally, a `customer_id`. A membership with a `customer_id` is
**customer-scoped**: that user sees only that customer's data. One person can belong to
several banks, with a separate membership in each.

Isolation has three layers:

1. **Repository filter (the one that matters).** `repositories/base.py::TenantScopedRepository`
   adds `WHERE tenant_id = :tenant` to every read and stamps `tenant_id` on every insert.
   `CustomerScopedRepository` also filters on `customer_id` when the caller is
   customer-scoped. The tenant id always comes from the authenticated token
   (`db/tenancy.py::TenantContext`, built in `auth/deps.py` or `web/deps.py`), never from a
   request body or path. `tests/test_api/test_cross_tenant_isolation.py` attacks every
   endpoint with another tenant's ids to prove this layer holds.
2. **Permission masking.** A customer-scoped membership can't hold tenant-wide
   permissions even if its security group grants them (`auth/deps.py`).
3. **Postgres row-level security (defence in depth).** Migration
   `6b9b23b81e31_postgres_row_level_security_defense_in_` forces RLS on the single-tenant
   operational tables. It's a no-op on SQLite and SQL Server. Every request, scheduled job
   and platform-operator call binds its session to one bank (`db/tenancy.py`), re-applied
   at the start of each transaction; a signed-in user can also see their own memberships
   in other banks, for switching organization. It only protects anything when the app
   connects as a role without `BYPASSRLS` (see README, "Postgres"). `exception_item` and
   `decision` are left out on purpose: training the shared ML model reads decisions from
   every bank that chose it (see [ML pipeline](#ml-pipeline)). If you need RLS there too,
   give the training job its own `BYPASSRLS` role rather than weakening the policy.

Customer-owned rows (`account`, `issued_item`, `paid_item`, `exception_item`, and so on)
carry a **denormalized `customer_id`** copied from their account at creation time. That
makes the customer filter a single-column `WHERE` rather than a join, and it's also what
per-customer ML training groups on.

The **platform operator** (whoever runs the OpenClear installation) is not a tenant. They act
through platform API keys (`domain/platform_api_key.py`, `auth/platform_api_key_deps.py`)
with explicit scopes, for example running the shared ML model (`api/v1/platform_ml.py`)
or reading usage metrics.

## Data model

The core tables, by area:

- **Check:** `issued_item` (what the customer wrote), `stop_payment`, `paid_item` (what
  was presented), `check_image` (front/back images, OCR results).
- **ACH:** `ach_authorization_rule` (which originators may debit an account, with
  optional receiver, amount, frequency and SEC-code limits), `ach_transaction`,
  `ach_return_reason`, and `bank_form` for each bank's own Written Statement of Unauthorized Debit
  form (shown to customers; the bank collects the statement).
- **Shared review core:** `exception_item` and `decision`. These are network-agnostic.
- **ML:** `ml_model` (one row per trained version, in a *slot*), `customer_ml_setting`.
- **Organisation:** `tenant`, `customer`, `user`, `tenant_membership`, `security_group`,
  `sso_connection`, `webauthn_credential`.
- **Operations:** `audit_log_entry` (append-only), `bulk_upload_file` and
  `bulk_upload_created_record` (so a bulk upload can be backed out), `notification`,
  `data_export_job`, `revoked_session`.

### Why `exception_item.source_item_id` has no foreign key

An exception can come from any network: a `paid_item` for checks, an `ach_transaction`
for ACH, and later perhaps an RTP payment. A typed foreign key per network would mean a new
nullable column (and a migration) on the shared table for every network. Instead,
`source_item_id` is a plain UUID plus `network_code`, and the only sanctioned way to
dereference it is `networks.registry.get_adapter(network_code).load_source_item(...)`.
The cost is that the database can't enforce the pointer, so
`tests/test_api/test_exception_pointer_integrity.py` checks it instead.
`related_reference_id` (the matched issued item or authorization rule) follows the same
pattern.

### Enums

Enums are stored with `native_enum=False`, so they're portable VARCHAR columns across
SQLite, Postgres and SQL Server. SQLAlchemy stores the member **name**, uppercase, not the
value. Keep that in mind when writing data migrations or raw SQL.

## Payment networks: the adapter pattern

Each network is a package under `networks/` (`check/`, `ach/`) that implements
`networks/base.py::NetworkAdapter` and registers itself with
`networks.registry.register_adapter()`:

| Method | Does |
|--------|------|
| `evaluate(session, transaction)` | Runs the network's matching rules and returns a `MatchResult` |
| `build_features(session, exception_item)` | Builds that network's ML feature dict |
| `load_source_item(session, source_item_id)` | Resolves `exception_item.source_item_id` |

Shared code (`services/exception_service.py`, `ml/predict.py`,
`api/v1/exceptions.py`, `api/v1/decisions.py`) only talks to the registry, never to a
concrete network. Adding a network takes two things: a package that registers its adapter as a plugin (see [Plugins](#plugins)) and a `payment_network` row. It doesn't touch `exception_item`, `decision`, `ml/` or the exceptions API.

`settlement_timing` is the hook for real-time networks. Check and ACH are
`ASYNC_REVIEWABLE`: accept the item, raise an exception, and let a person review it before
settlement. A `SYNCHRONOUS_AUTHORIZATION` network such as RTP would need `evaluate()`
called inline, with the ingestion request blocking on the answer. That's a different call
pattern in the ingestion service, not a schema change.

Each package typically has `types.py` (inputs/enums), `rules.py` (pure rule function),
`adapter.py` (loads the facts from the database and calls the rules), `ingestion.py`
(creates the transaction and the exception), `features.py` and `bulk_import.py`.

## Matching rules

Rules are **pure functions**: `networks/check/rules.py::evaluate_check_rules` and
`networks/ach/rules.py::evaluate_ach_rules` take a snapshot of pre-loaded facts and return
`(exception_types, related_reference_id)`. The adapter does all the database work. That
keeps the rules trivially unit-testable and makes their order easy to read.

### Check rules, in order

1. **Duplicate paid**: another item with the same account, check number and amount has
   already been paid. It's recorded, but checking continues, so the reviewer also sees
   *why else* the item might be wrong.
2. **Stop payment**: an active stop matches. **Stops here.** A stopped check is returned
   whatever else is true, so further findings would just be noise.
3. **Not in file**: no issued item with this account and check number. **Stops here**,
   because every remaining rule compares against the issued item.
4. **Voided**: the issued item was voided.
5. **Amount mismatch**: the presented amount differs from the issued amount (exact
   comparison).
6. **Payee mismatch**: only when OCR produced a payee. A fuzzy `token_set_ratio` on
   normalised names below `payee_match_fuzzy_threshold`. Without OCR, this rule can't fire.
7. **Stale dated**: presented more than the bank's `stale_date_threshold_days` after
   issue.

Rules 4–7 accumulate, so one item can carry several reasons ("Amount mismatch, Stale
dated").

### ACH rules, in order

Only **debits** are checked, since ACH positive pay is about money leaving the account.
Credits always clear.

1. The account blocks all debits → **unauthorized originator**.
2. No authorization rule for this originator → **unauthorized originator**.
3. Choose the rule: an exact `receiver_id` match beats a wildcard rule (`receiver_id` is
   null). That lets a customer allow a company in general but tighten limits for one
   receiver. If the originator has rules but none covers this receiver → **receiver ID
   not permitted**, a different signal for the reviewer from "never heard of them".
4. Against the chosen rule, accumulating: **amount exceeds limit**, **frequency exceeded**
   (prior debits in the period ≥ the limit), **SEC code not permitted**.

An exception can be turned into an allow-list rule from its detail page, so the same
pattern clears next time.

## From exception to decision

When a rule fires, ingestion (`networks/<code>/ingestion.py`):

1. creates the `exception_item` with its reasons and a **decision deadline** when the
   customer has a default disposition (`services/auto_disposition_service.py`);
2. scores it (`ml/predict.py::score_exception`; see below);
3. queues notifications (`services/notification_service.py`).

A person then decides, through `services/decision_service.py`:

- **Single control:** one user with the decide permission records pay or return.
- **Dual control** (maker/checker, per bank): one user *recommends*
  (`submit_recommendation`, status `pending_approval`) and a **different** user
  approves or rejects it (`decide`). The service enforces maker ≠ checker.

`decide()` is the ML feedback point. It snapshots the exception's current feature vector
into `decision.features_json`, so later training uses the facts as they were when the
decision was made.

If nobody decides by the deadline, the scheduled sweep
(`workers/tasks.py::sweep_expired_dispositions_job`) applies the customer's default: always
pay, always return, or follow the model's score. It leaves the item alone if the choice
can't be made safely (no model yet, or an ACH return with no default return reason).

A decision made before the item's cut-off can be changed or taken back until then; at the
cut-off it locks and goes out (feed number, `exception.decided`, decision files). That runs
every minute (`decision_lock_interval_seconds`, `workers/tasks.py::decision_lock_job`), each
decision in its own transaction.

**One item, one lock.** Every change to an item's decision state (recommend, withdraw, decide,
change, undo, the default at the deadline, the lock at the cut-off) first takes the item's row
lock (`ExceptionRepository.get_for_update`) and reads it fresh, so two people, or a person and
the cut-off, can't both act on what they each read. The scheduled jobs pass `skip_locked` and
leave a busy item for their next run.

**Lock order.** Two per-bank transaction locks exist on Postgres: the action log's
(`audit_log_service._chain_lock_key`) and the decision feed's (`decision_feed_service._lock_key`).
Take the action log's first, always; `assign_sequence` does, so any new code that numbers a
decision inherits the order. Taking the two in opposite orders in two transactions deadlocks.

Evidence for a decision (images, OCR, matched issued item, rule details) is assembled by
`services/exception_evidence.py`. Recommendations, decisions and other significant actions
are written to the append-only audit log (`services/audit_log_service.py`): a signed hash
chain per bank, numbered by `sequence`, safe under concurrent writers (see README,
"Immutable action log").

## ML pipeline

### What the score means

`exception_item.ml_score` is the model's probability that the item should be **paid**. The
UI shows the inverse as "Fraud risk" (low/medium/high). No model means no score, never a
made-up 0.5, so the UI can tell "unknown" from "uncertain".

### Model choice

The model is **logistic regression** over a `DictVectorizer` (`ml/model.py`). That's
deliberate: in a fraud-adjacent product, "why was this flagged?" needs an answer a
coefficient can give. Anything else can sit behind the same `ScoringModel` protocol later.

There's **one model per network**. Check and ACH exceptions have different facts (payee
similarity and OCR confidence against SEC codes and receiver IDs), so each network's
`features.py` builds its own feature shape. A single combined model would be mostly
zero-filled columns.

### Slots: shared, bank-only, customer

`ml/registry.py` keeps models in **slots**. Each slot has at most one `active` model plus
its history:

| Slot | Trained on | Run by |
|------|-----------|--------|
| Shared (per network) | Decisions from every bank that chose the shared model. A bank's fraud-training examples count only once the platform operator approves them. | Platform operator (`/api/v1/platform/ml/*`) |
| Bank-only | That bank's own decisions, including its fraud-training examples | The bank's admins |
| Customer | That customer's decisions | The bank's admins |

Each bank chooses the shared or bank-only model (`tenant.ml_model_source`,
`services/tenant_ml_service.py`). New banks start on the shared model, which solves the
cold-start problem: a new bank gets useful scores on day one. A new bank can't switch to a bank-only
model for its first 90 days (`ml_new_bank_private_switch_lock_days`); the platform
operator can lift that early. When a bank does switch, the shared model is **copied** into
its bank-only slot, so it keeps scoring while it builds up its own data, and its decisions
stop feeding the shared model. Switching back is never locked, but the bank must
acknowledge the data-pooling disclosure first.

Which model scores an exception (`ml/predict.py::_resolve_scoring_source`):

1. A customer's exception uses that customer's own model if it has an active one and its
   setting isn't "Bank's model" (`customer_ml_setting`, default AUTO).
2. Otherwise, the bank's choice: bank-only or shared.

### Training and promotion

`workers/tasks.py::retrain_job` queues a retrain job for every slot that has enough new
labelled decisions (counted with SQL `COUNT`s, against the number of eligible decisions
the slot's last run recorded), and the job calls `ml/train.py::train_model`. Training
reads only the two columns it needs (`features_json`, outcome), in batches, from the last
`ml_training_window_days` (365) and at most the newest `ml_training_max_rows` (250,000);
known-fraud training examples are kept whatever their age. Each run records its row
counts, whether it was capped, and how long it took in `metrics_json["training"]`.
Training holds out the most recent decisions. The
new model (challenger) and the active one (champion) are scored on the **same** holdout,
and the challenger is promoted only if it does at least as well
(`_promotion_decision`). A bank's first model trained on its own data must also have seen
at least `ml_bank_model_min_decisions`, so it can't beat the seeded copy by luck on a tiny
sample. There's a per-slot cooldown so on-demand retraining can't be used to burn compute.

Artifacts are joblib (pickle) files under `ml_artifact_dir` (`ml/registry.py::ArtifactStore`).
The database holds each file's path and its SHA-256 (`ml_model.artifact_sha256`), and a
file is unpickled only if it still matches, because unpickling a swapped file would run
code. A model that fails the check simply doesn't score, and the failure is logged. `ml/predict.py` caches loaded models per slot (least recently used dropped
past `ml_model_cache_size`, 256) and reloads automatically when the slot's active model
id changes. A model file that's missing or unreadable is treated the same way: no score,
logged.

## OCR

`ocr/base.py` defines the `OCRProvider` protocol, and `ocr/factory.py` picks the provider
from config. The default, `ocr/tesseract_provider.py`, is local and needs no cloud
account, but it's a general-purpose engine: it finds the amount and payee with regular
expressions after preprocessing (`ocr/preprocessing.py`). It's fine for trials and printed
checks, but not accurate enough for high-volume handwritten checks. That's the OCR risk,
and it's why the provider is swappable. AWS Textract and Azure Document Intelligence
(`textract_provider.py`, `azure_di_provider.py`) are **stubs**, and startup refuses to run
with them configured.

OCR runs as a background job after an image upload
(`networks/check/ocr_processing.py`). A failure or timeout marks the image as failed, but
never blocks the paid item. Only the payee rule uses OCR output.

## Bulk ingestion

Files arrive through the UI, the API, or a watched **dropbox** directory
(`services/dropbox_import_service.py`, run by `dropbox_import_job`). Parsers live in
`bulk_import/` (CSV, NACHA, X9.37, ZIP of images + CSV).

- **Parsing is all-or-nothing.** A file that can't be parsed, or whose control totals don't
  add up (NACHA batch/file controls; X9.37 bundle, cash-letter and file controls), is
  rejected before anything is written.
- **Files are imported by a background job** (`services/bulk_import_service.py`, job
  `bulk_upload.import`). The request (or dropbox scan, or an API call over
  `bulk_api_sync_max_rows`) stores and signs the file and parses it once to reject a bad
  file whole; the job parses it again from storage and imports it in chunks
  (`bulk_import_chunk_rows`, 1,000). Each row runs in a savepoint, so one bad row (an
  unknown account, a duplicate) fails on its own. Each chunk commits once, together with
  its `bulk_upload_row_result` rows, its audit entries (chained in one batch,
  `audit_log_service.record_actions`), its `bulk_upload_created_record` rows and
  `bulk_upload_file.processed_rows`, so a retried job resumes after the last committed
  chunk without importing anything twice. On SQLite each chunk opens with a write,
  because the driver doesn't start a transaction before one and a savepoint outside a
  transaction commits on release.
- **Per-exception notifications are held during an import**
  (`notification_service.exceptions_summarized`); the job counts new exceptions per
  customer and sends each reviewer one summary at the end.
- **Large files stay on disk.** Uploads are copied to storage in pieces and hashed and
  signed as they're written (`sign_digest`); X9.37 files are read through an mmap with
  images sliced out per check, and zip images are read on demand.
- The API's small `/bulk` calls (up to `bulk_api_sync_max_rows`) still import inside the
  request (`ingest_paid_items_bulk` and friends), and so do the user and fraud-training
  uploads, which are small and need an answer on the spot.
- Every row created is recorded in `bulk_upload_created_record`, so the whole upload can be
  **backed out** later (`services/bulk_upload_reversal_service.py`). Backing out withdraws
  exceptions that haven't been decided yet, and never overrides a human decision.

## Authentication and sessions

- **Tokens:** JWTs signed with ECDSA (`auth/security.py`, keys from `auth/keys.py`): an
  access token (30 minutes by default) and a refresh token (7 days). A bank can override
  both. The payload carries user, tenant and membership, but permissions come from the
  membership's *current* security group on every request (`auth/deps.py`), so a change to
  a group or a deactivated membership takes effect on the next request.
- **Sessions:** in the UI, activity keeps the session alive, and the page warns 5 minutes
  before it would expire (`static/js/session.js`). Logging out ends that session. An admin
  "sign out everywhere", a password change and an admin password reset end all of a
  person's sessions (`services/session_service.py`, recorded in `revoked_session`, which
  every request checks).
- **Second factor:** WebAuthn (`auth/webauthn_service.py`). Challenges are stored in the
  database with a 5-minute lifetime. A registered key belongs to one bank
  (`webauthn_credential.tenant_id`), not to the person, so someone in two banks registers
  a key in each. That costs people in several banks a little extra setup, but a key
  registered at one bank never grants anything at another. A bank can require a key for a
  membership (`tenant_membership.require_webauthn`).
- **SSO:** OIDC per bank, or per customer (`auth/oidc_service.py`, `services/sso_service.py`).
  Login state and nonce travel in a signed, short-lived cookie. IdP groups map to security
  groups.
- **Outbound HTTP** (OIDC discovery and token calls) goes through `auth/outbound_http.py`,
  which refuses private, loopback and link-local addresses (including via DNS rebinding),
  so a bank admin can't point an SSO connection at internal services.

## Web UI vs. JSON API

The UI (`web/routers/`) calls the **service layer directly, in-process**. It never calls
its own JSON API over HTTP. That avoids a second network hop, a second copy of every
error-mapping path, and having to hold a bearer token in the browser. The two front ends
use separate credentials: the UI reads an HttpOnly `access_token` cookie (`__Host-access_token` over HTTPS)
(`web/deps.py`), and the API reads the `Authorization` header (`auth/deps.py`). Neither
accepts the other's.

Because the UI uses cookies, every `/ui/*` POST carries a double-submit CSRF token
(`web/security.py`). Pages use a strict per-request nonce CSP, so there are no inline
scripts or styles. Templates hide buttons with `can(ctx, permission)`, but that's
cosmetic: the route's own permission check is what enforces access.

The WebAuthn login routes follow the same rule. The browser script
(`static/js/webauthn.js`) only handles the options and credential JSON, and never sees a
token.

## Data exports

A bank admin can export all of the bank's data; a customer admin can export their own
customer's data (`services/data_export_service.py`). Both produce a ZIP of JSON files
and images, built by a background job that streams each table to disk in batches (so
there's no size limit, only a time limit).

| Included | Bank-wide export | Customer export |
|----------|:---:|:---:|
| Accounts, issued items, stops, paid items, ACH rules and transactions, exceptions, decisions, check images | ✓ | ✓ (that customer's only) |
| Users (email, active flag, security group **name**), SSO connections and group mappings | ✓ | ✓ (that customer's only) |
| Customers list, bulk upload files, audit log | ✓ | ✗ |

The last row is bank-wide only: those rows either can't be attributed to one customer, or
would reveal the bank's own configuration and activity to a customer's admin. Password
hashes and other secrets are never exported, and a final filter (`_SECRET_COLUMNS`) strips
them even if a future change adds them by mistake.

## Billing

`services/billing/` turns activity into charges (BILLING_PLAN.md):

- `metrics.py` counts each metric for a period, grouped by account, customer or bank.
  Monthly metrics are a census of what's active at the end of the period; activity
  metrics count rows created in it. Training backfill isn't counted.
- `pricing.py` prices one fee for one customer: unit price or step tiers, then the
  minimum, a negotiated price, the discount and waivers. Each line carries its camt.086
  charge method and payment method (`BCMP` for analysis, `FLAT` for hard charge).
- `statements.py` builds one statement per customer plus the bank-wide one, live.
  `finalize.py` copies them into `billing_statement` / `billing_statement_line` as a
  numbered version; the exports (`exports.py`: analysis feed, postings, camt.086) read
  only those frozen rows, so a later fee change can't alter what was sent.
- `platform.py` prices each bank's whole organization against the operator's
  `platform_fee_item` schedule, binding the session to each bank in turn so RLS still
  applies. That table has no `tenant_id` and no RLS; only a `billing`-scoped platform key
  reaches it.

Billing tables carry `tenant_id` and the same RLS policy as everything else.

## Background work and multiple instances

Two kinds of background work:

- **Scheduled jobs** (`workers/tasks.py`, started by `workers/scheduler.py` when their
  `OPENCLEAR_*` flags are on): ML retrain, dropbox import, notification sending, the
  expired-disposition sweep, and the demo tenant reset. On Postgres each run takes a
  per-job advisory lock (`workers/leader_lock.py`), so with several instances only one
  runs each tick. The jobs are plain functions, so an external cron can call them instead.
- **Background jobs** (`workers/jobs.py`, handlers in `workers/job_handlers.py`): OCR after
  an image upload, data exports, each bank's dropbox scan and each model slot's retrain.
  A job is a row in the `job` table, committed in the same transaction as whatever queued
  it. Workers claim due jobs with a conditional update (and `SKIP LOCKED` on Postgres), run
  the handler with a session bound to the job's bank (so RLS applies), send heartbeats, and
  retry failures with back-off; a job whose heartbeats stop is queued again. Workers run as
  a thread in each web process (`worker_mode=in_process`, the default) or as separate
  `openclear-worker` processes (`external`). The scheduler's retrain and dropbox jobs now
  just queue one job per model slot or bank. The sweep, notification sending and the demo
  reset are quick, and still run directly.

Shared between instances (SCALE_PLAN.md S6):

- **Files** go through `storage.py`: local directories by default, or an S3-compatible
  bucket (`storage_backend = "s3"`). The database stores a reference (a path, or
  `s3://bucket/key`) and reads dispatch on it, so switching backends doesn't strand older
  files; `scripts/move_files_to_object_storage.py` moves them. Downloads stream, and code
  that needs a real file (an mmap of a cash letter, a zip opened by path) asks for a local
  copy (`storage.local_path`). Exports are built in a temporary file and then stored. ML
  artifacts keep their SHA-256 check whatever the backend.
- **Rate limits** are per process by default, or a Redis sliding window
  (`RedisRateLimiter`: one Lua script trims, checks and records atomically using Redis's
  clock). It fails open, logging, if Redis is unreachable.
- **Connection pools** are sized by `db_pool_size` / `db_max_overflow`.

Still per process: the OIDC discovery and key caches, and the loaded-model cache. See
README, "Running more than one instance".

## Plugins

`plugins/__init__.py` holds a registry of extension points:

- networks;
- OCR providers;
- email and SMS providers;
- storage backends;
- scoring models;
- import formats.

`plugins.load()` runs once at startup, from both `main.py` and `workers/run.py`, in this order:

1. It registers the built-ins from `plugins/builtin.py`.
2. It loads each entry point in the `openclear.plugins` group that `settings.plugins` allows.

The factories look up names in the registry rather than importing concrete classes:

- `ocr/factory.py`;
- the email and SMS factories;
- `storage.backend()`;
- `ml/train.py`, through `settings.ml_scoring_model`;
- `bulk_import_service.spec_for()`.

So a built-in and a plugin are used the same way.

A plugin is checked in several ways:

- A plugin declares the `PLUGIN_API_VERSION` range it supports. One outside that range is marked `incompatible` and isn't called.
- Registration is all-or-nothing. A plugin adds to a staging copy of the registry, and the copy replaces the live one only if `register()` succeeds. A plugin that raises, or claims a name that's already taken, leaves nothing behind and is marked `failed`.
- `plugins.status()` feeds Settings → Plugins.

Some extension points are a bank's choice rather than the operator's (`BANK_CHOICE_POINTS`): import formats, decision layouts, matching rules and notification channels. Scoring features aren't a bank's choice: they feed the shared model, so they apply to every bank.

- `registry.origins` records which plugin registered each entry.
- A plugin's entry is usable by a bank only if the bank has a `tenant_extension` row for it.
- `services/plugin_service.require_enabled` checks this at the points of use: `bulk_import_service.receive_upload` and `GET /api/v1/decisions/file`.
- Built-in entries need no row.

`plugins/api.py` is the stable surface plugins import.

The phases are in PLUGIN_PLAN.md:

- PL3 added matching rules, scoring features and notification channels.

Where the PL3 extension points run:

- **Matching rules:** in `CheckAdapter`/`AchAdapter.evaluate`, through `plugin_service.apply_matching_rules`.
  - Each rule runs in a savepoint.
  - A rule can only add exception codes.
  - A rule that fails adds `plugin_rule_failed`, so the item fails closed.
- **Scoring features:** in `build_features`, through `plugin_service.extra_features`.
- **Notification channels:** `webhook_service.emit` queues a `plugin.notify` job per channel the bank turned on.

A network plugin calls the same helpers from `plugins.api`.

## Data growth

- **Housekeeping** (`services/housekeeping_service.py`, daily): finished jobs,
  notifications, bulk-upload row results and expired export archives, each after its own
  `retention_*_days`, deleted in batches of 5,000 with a commit each. Business records are
  never touched.
- **Audit archiving** (`services/audit_archive_service.py`, opt-in): under the bank's
  audit lock, the oldest entries are verified (signature, and the link from the previous
  checkpoint), written to a gzipped JSON Lines file, stored, fingerprinted and signed;
  an `audit_log_checkpoint` records the range and last hash, and the entries are deleted
  in the same transaction. `verify_chain` starts from the latest checkpoint;
  `verify_archives` re-checks every file and that consecutive checkpoints link. The
  newest entry is never archived, so appending never needs the checkpoint.
- **Query budgets** (`tests/test_auth/test_query_budget.py`): statements per bulk-import
  row, and list pages that must not query per row, so a scale regression fails CI.
- **Partitioning** isn't used. S0 showed pages and queries fast at 500,000 checks with
  S2's indexes; revisit (monthly partitions of `paid_item`, `ach_transaction`,
  `audit_log_entry`) when a real bank's volumes say so.

## Known gaps

- **Historical check features** (issuer exception rate, amount z-score against the
  issuer's history, check-number gaps) were planned but aren't built. The check model
  uses per-item and OCR features only (`networks/check/features.py`).
- **Cloud OCR providers** are stubs (see [OCR](#ocr)).
- **X9.37 field positions** follow the published layouts but haven't been checked against
  a real processor's file (`bulk_import/x937.py`).
- **Postgres-specific features** (RLS, the scheduler lock) are covered by unit tests but
  haven't been run against a live Postgres server in this repo's CI.
